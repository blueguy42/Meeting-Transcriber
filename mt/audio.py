"""Shrink recordings after transcription, using macOS's built-in `afconvert` (no extra install).

Recording stays WAV (survives a crash); once the transcript is safely saved, each track is
compressed and the WAV is removed - but only after the compressed file is verified.
"""

import re
import subprocess
import sys
from pathlib import Path

TRACKS = ("mic", "system")
RECORDINGS = "recordings"
FORMATS = {  # setting -> (extension, afconvert args)
    "aac": ("m4a", ["-f", "m4af", "-d", "aac", "-b", "32000", "-c", "1"]),
    "flac": ("flac", ["-f", "flac", "-d", "flac"]),
}


def audio_dir(meeting_dir: Path) -> Path:
    """Where the tracks live: recordings/ (older meetings kept them in the meeting folder itself)."""
    d = meeting_dir / RECORDINGS
    return d if d.is_dir() else meeting_dir


def _duration(path: Path) -> float | None:
    r = subprocess.run(["afinfo", str(path)], capture_output=True, text=True)
    m = re.search(r"estimated duration:\s*([\d.]+)", r.stdout)
    return float(m.group(1)) if m else None


def compress_meeting(meeting_dir: Path, mode: str) -> list[str]:
    """Returns human-readable notes for anything that could not be compressed (WAV is kept then)."""
    if mode not in FORMATS or sys.platform != "darwin":
        return []
    ext, args = FORMATS[mode]
    base = audio_dir(meeting_dir)
    notes = []
    for name in TRACKS:
        wav, dst = base / f"{name}.wav", base / f"{name}.{ext}"
        if not wav.exists():
            continue
        try:
            r = subprocess.run(["afconvert", *args, str(wav), str(dst)], capture_output=True, text=True)
            d_wav, d_dst = _duration(wav), _duration(dst)
            if r.returncode != 0 or not d_wav or not d_dst or abs(d_wav - d_dst) > 1.0:
                raise RuntimeError(r.stderr.strip() or f"duration mismatch ({d_wav} vs {d_dst})")
            wav.unlink()
        except Exception as e:
            dst.unlink(missing_ok=True)
            notes.append(f"{name}: kept WAV ({e})")
    return notes


def find_track(meeting_dir: Path, name: str, tmp: Path) -> Path | None:
    """The WAV if it exists, else decode a compressed copy to a temporary 16 kHz WAV."""
    base = audio_dir(meeting_dir)
    wav = base / f"{name}.wav"
    if wav.exists():
        return wav
    for ext in (e for e, _ in FORMATS.values()):
        src = base / f"{name}.{ext}"
        if src.exists():
            out = tmp / f"{name}.wav"
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(out)],
                check=True, capture_output=True,
            )
            return out
    return None
