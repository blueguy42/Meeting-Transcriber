import re
import shutil
from pathlib import Path

from ..meetings.audio import compress_meeting, has_audio
from ..meetings.meeting import Meeting
from ..settings.model import Settings
from ..summary.summarize import summarize_meeting
from ..transcription import transcribe_meeting
from .progress import DOCX, SUMMARIZING

UNTITLED = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}(-\d+)?$")  # e.g. 2026-10-07_1030


def process(meeting: Meeting, cfg: Settings, prompt_name: str | None = None, progress=print) -> tuple[Meeting, Path]:
    """transcribe -> (optionally) summarize -> .docx -> name the folder. Returns (the meeting, the best file)."""
    transcript = transcribe_meeting(meeting, cfg, progress)
    for note in compress_meeting(meeting, cfg.compress_audio):
        progress(f"Audio compression: {note}")
    if not cfg.auto_summarize:
        return meeting, transcript
    progress(SUMMARIZING)
    return finish(meeting, cfg, summarize_meeting(meeting, cfg, prompt_name), progress)


def transcribe_existing(meeting: Meeting, cfg: Settings, prompt_name: str | None = None, progress=print) -> tuple[Meeting, Path]:
    """Transcribe a recorded meeting (again: after changing the model, language or vocabulary, say), then summarize it
    again if that is on. An existing transcript.md is left alone until the transcription and the speaker separation
    have both finished, and is then replaced by the new one (if anything fails before that, the old one is still there)."""
    if not has_audio(meeting):
        raise ValueError("This meeting has no recording to transcribe (the audio was deleted).")
    return process(meeting, cfg, prompt_name, progress)


def summarize_existing(meeting: Meeting, cfg: Settings, prompt_name: str | None = None, progress=print) -> tuple[Meeting, Path]:
    """(Re-)summarize a meeting folder whatever auto_summarize says; transcribes first if it has no transcript yet."""
    if not meeting.has_transcript:
        transcribe_meeting(meeting, cfg, progress)
        for note in compress_meeting(meeting, cfg.compress_audio):
            progress(f"Audio compression: {note}")
    progress(SUMMARIZING)
    return finish(meeting, cfg, summarize_meeting(meeting, cfg, prompt_name), progress)


def finish(meeting: Meeting, cfg: Settings, summary: Path, progress=print) -> tuple[Meeting, Path]:
    """After a summary exists: Word export, then add the title to the folder name."""
    out = make_docx(meeting, cfg, summary, progress) or summary
    renamed = rename_with_title(meeting, summary)
    return renamed, renamed.folder / out.name


def make_docx(meeting: Meeting, cfg: Settings, summary: Path, progress=print) -> Path | None:
    """A failure here never loses the Markdown summary. Applies to every summary, automatic or manual."""
    if not cfg.export_docx:
        return None
    progress(DOCX)
    try:
        from ..summary.docx_export import export_docx

        return export_docx(meeting, summary)
    except Exception as e:
        progress(f"Word export failed (Markdown summary kept): {str(e)[:120]}")
        return None


def clean_title(raw: str, limit: int = 60) -> str:
    t = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", raw)  # characters Finder/Windows/Drive dislike
    t = re.sub(r"\s+", " ", t).strip(" .-")
    return t[:limit].rstrip(" .-")


def rename_with_title(meeting: Meeting, summary: Path) -> Meeting:
    """2026-10-07_1030 -> '2026-10-07_1030 - Q4 Marketing Budget Review'. Only the first summary
    names the folder; re-summarizing with another style never renames it again."""
    if not UNTITLED.match(meeting.name):
        return meeting
    m = re.search(r"^#\s+(.+)$", summary.read_text(), flags=re.M)
    title = clean_title(m.group(1)) if m else ""
    if not title:
        return meeting
    target = meeting.folder.with_name(f"{meeting.name} - {title}")
    n = 2
    while target.exists():
        target = meeting.folder.with_name(f"{meeting.name} - {title} ({n})")
        n += 1
    try:
        return Meeting(shutil.move(str(meeting.folder), str(target)))
    except OSError:
        return meeting
