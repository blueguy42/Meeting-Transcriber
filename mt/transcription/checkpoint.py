"""What a paused transcription has done so far, so Resume continues from the next speech burst."""

import json
import time
from pathlib import Path

import numpy as np


PARTIAL = "transcribe.partial.json"
SAVE_EVERY_S = 5.0


class Checkpoint:
    """What a paused transcription has done so far, so Resume continues from the next speech burst. One entry per track
    (the burst results and the language guess carried between bursts), valid only for the same model, language,
    vocabulary and speech bursts: anything else and the track starts again."""

    def __init__(self, meeting_dir: Path):
        self.path = Path(meeting_dir) / PARTIAL
        self.tracks: dict = {}
        self._saved = time.monotonic()
        try:
            self.tracks = json.loads(self.path.read_text()).get("tracks", {})
        except (OSError, ValueError):
            pass

    @staticmethod
    def fingerprint(cfg: dict, audio: np.ndarray, regions: list) -> str:
        import hashlib

        key = json.dumps([cfg["model"], cfg["language"], list(cfg["allowed_languages"]), list(cfg["vocabulary"]), len(audio), [list(map(int, r)) for r in regions]])
        return hashlib.sha1(key.encode()).hexdigest()

    def resume(self, label: str, fp: str) -> tuple[int, list, dict]:
        t = self.tracks.get(label)
        if t and t.get("fp") == fp:
            return int(t["done"]), list(t["out"]), dict(t["state"])
        return 0, [], {}

    def update(self, label: str, fp: str, done: int, out: list, state: dict) -> None:
        self.tracks[label] = {"fp": fp, "done": done, "out": out, "state": state}
        if time.monotonic() - self._saved >= SAVE_EVERY_S:
            self.save()

    def save(self) -> None:
        if not self.tracks:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tracks": self.tracks}))
            tmp.replace(self.path)
        except OSError:
            pass  # a checkpoint is a convenience: never fail the job over it
        self._saved = time.monotonic()

    def clear(self) -> None:
        self.tracks = {}
        try:
            self.path.unlink()
        except OSError:
            pass


def clear_checkpoint(meeting_dir: Path) -> None:
    Checkpoint(meeting_dir).clear()
