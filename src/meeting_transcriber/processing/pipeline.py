import re
import shutil
from pathlib import Path

from ..meetings import speakers
from ..meetings.audio import compress_meeting, has_audio
from ..meetings.meeting import Meeting
from ..settings import config
from ..settings.model import Settings
from ..summary.name_speakers import name_speakers
from ..summary.summarize import summarize_meeting
from ..transcription import transcribe_meeting
from .control import Stopped
from .progress import DOCX, NAMING, NAMING_SKIPPED, SUMMARIZING

UNTITLED = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}(-\d+)?$")  # e.g. 2026-10-07_1030


def process(meeting: Meeting, cfg: Settings, prompt_name: str | None = None, progress=print) -> tuple[Meeting, Path]:
    """transcribe -> (optionally) summarize -> .docx -> name the folder. Returns (the meeting, the best file)."""
    transcript = transcribe_meeting(meeting, cfg, progress)
    for note in compress_meeting(meeting, cfg.compress_audio):
        progress(f"Audio compression: {note}")
    name_the_speakers(meeting, cfg, progress)
    if not cfg.auto_summarize:
        return meeting, transcript
    progress(SUMMARIZING)
    return finish(meeting, cfg, summarize_meeting(meeting, cfg, prompt_name), progress)


def naming_wanted(meeting: Meeting, cfg: Settings) -> bool:
    """Is it worth asking Claude for the speakers' names: it is on, the other people were told apart, Claude Code is there."""
    return bool(cfg.name_speakers and cfg.diarize and config.find_claude(cfg) and speakers.raw_labels(meeting, cfg))


def name_the_speakers(meeting: Meeting, cfg: Settings, progress=print) -> None:
    """After a transcript is written: names the user chose stay, then Claude names the others it has proof for.
    Never fails the meeting: without names the transcript is just as good as it was."""
    speakers.apply_saved(meeting, cfg)
    if not naming_wanted(meeting, cfg):
        return
    progress(NAMING)
    try:
        name_speakers(meeting, cfg)
    except Stopped:
        raise
    except Exception as e:
        progress(f"{NAMING_SKIPPED}{str(e)[:120]}")


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
        name_the_speakers(meeting, cfg, progress)
    progress(SUMMARIZING)
    return finish(meeting, cfg, summarize_meeting(meeting, cfg, prompt_name), progress)


def finish(meeting: Meeting, cfg: Settings, summary: Path, progress=print) -> tuple[Meeting, Path]:
    """After a summary exists: Word export, then add the title to the folder name."""
    out = make_docx(meeting, cfg, summary, progress) or summary
    renamed = rename_with_title(meeting, summary)
    speakers.mark_summary_current(renamed)  # the summary now uses the names the transcript has
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


def name_existing(meeting: Meeting, cfg: Settings, progress=print) -> tuple[Meeting, Path]:
    """Name the speakers of a finished meeting (asks Claude, whatever the 'name speakers' setting says: you asked)."""
    if not meeting.has_transcript:
        raise ValueError("This meeting has no transcript yet.")
    if not speakers.raw_labels(meeting, cfg):
        raise ValueError("The transcript has no separate speakers to name (speakers were not told apart).")
    progress(NAMING)
    name_speakers(meeting, cfg)
    return meeting, meeting.transcript_file
