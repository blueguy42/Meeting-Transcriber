"""A meeting folder. This is the one place that knows what is inside it and what each file is called:

    meta.json                      what the recording knew (start time, offsets, pauses, device changes)
    recordings/mic.wav, system.wav the audio (compressed to .m4a / .flac after transcribing)
    shots/shot_<seconds>.jpg       screenshots, named by the second of the recording they were taken
    notes.txt                      what you typed in the notes panel
    documents/                     the documents you added (and, in extracted/, the text read from them)
    transcribe.partial.json        where a paused transcription stopped
    transcript.md, summary-<style>.md, summary.docx   the results
"""

import json
import os
from pathlib import Path

RECORDINGS = "recordings"


class Meeting:
    def __init__(self, folder: Path | str):
        self.folder = Path(folder)

    def __repr__(self) -> str:
        return f"Meeting({str(self.folder)!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Meeting) and self.folder == other.folder

    def __hash__(self) -> int:
        return hash(self.folder)

    @property
    def name(self) -> str:
        return self.folder.name

    # ---- where things are
    @property
    def meta_file(self) -> Path:
        return self.folder / "meta.json"

    @property
    def shots_dir(self) -> Path:
        return self.folder / "shots"

    @property
    def recordings_dir(self) -> Path:
        return self.folder / RECORDINGS

    @property
    def audio_dir(self) -> Path:
        """Where the tracks live: recordings/ (older meetings kept them in the meeting folder itself)."""
        return self.recordings_dir if self.recordings_dir.is_dir() else self.folder

    @property
    def notes_file(self) -> Path:
        return self.folder / "notes.txt"

    @property
    def documents_dir(self) -> Path:
        return self.folder / "documents"

    @property
    def extracted_dir(self) -> Path:
        return self.documents_dir / "extracted"

    @property
    def documents_manifest(self) -> Path:
        return self.documents_dir / "documents.json"

    @property
    def partial_file(self) -> Path:
        return self.folder / "transcribe.partial.json"

    @property
    def transcript_file(self) -> Path:
        legacy = self.folder / "transcript" / "transcript.md"  # older meetings
        new = self.folder / "transcript.md"
        return legacy if legacy.exists() and not new.exists() else new

    @property
    def new_transcript_file(self) -> Path:
        """Where a transcript is written (always the current location)."""
        return self.folder / "transcript.md"

    @property
    def docx_file(self) -> Path:
        return self.folder / "summary.docx"

    def summary_file(self, style: str) -> Path:
        return self.folder / f"summary-{Path(style).name}.md"

    def summaries(self) -> list[Path]:
        """The summary-<style>.md files, newest first."""
        return sorted(self.folder.glob("summary-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)

    def shot_file(self, seconds: int) -> Path:
        return self.shots_dir / f"shot_{seconds:05d}.jpg"

    def shot_files(self) -> list[Path]:
        return sorted(self.shots_dir.glob("shot_*.jpg"))

    # ---- what is there
    @property
    def has_recording(self) -> bool:
        """Was it recorded by this app (it has meta.json)?"""
        return self.meta_file.exists()

    @property
    def has_transcript(self) -> bool:
        return self.transcript_file.exists()

    # ---- meta.json
    def read_meta(self) -> dict:
        return json.loads(self.meta_file.read_text())

    def write_meta(self, meta: dict) -> None:
        """Replace meta.json in one step: a reader (or a crash) sees the old file or the new one, never half of one."""
        tmp = self.meta_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        os.replace(tmp, self.meta_file)

    # ---- the folder itself
    def create(self) -> None:
        """A fresh meeting folder; it must not exist yet."""
        self.folder.mkdir(parents=True)
        self.shots_dir.mkdir()
        self.recordings_dir.mkdir()
