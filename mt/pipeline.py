import re
import shutil
from pathlib import Path

from .audio import compress_meeting
from .summarize import summarize_meeting
from .transcribe import transcribe_meeting, transcript_file

UNTITLED = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}(-\d+)?$")  # e.g. 2026-10-07_1030


def process(meeting_dir: Path, cfg: dict, prompt_name: str | None = None, progress=print) -> tuple[Path, Path]:
    """transcribe -> (optionally) summarize -> .docx -> name the folder. Returns (meeting_dir, best file)."""
    transcript = transcribe_meeting(meeting_dir, cfg, progress)
    for note in compress_meeting(meeting_dir, cfg["compress_audio"]):
        progress(f"Audio compression: {note}")
    if not cfg["auto_summarize"]:
        return meeting_dir, transcript
    progress("Summarizing with Claude…")
    return finish(meeting_dir, cfg, summarize_meeting(meeting_dir, cfg, prompt_name), progress)


def summarize_existing(meeting_dir: Path, cfg: dict, prompt_name: str | None = None, progress=print) -> tuple[Path, Path]:
    """(Re-)summarize a meeting folder whatever auto_summarize says; transcribes first if it has no transcript yet."""
    if not transcript_file(meeting_dir).exists():
        transcribe_meeting(meeting_dir, cfg, progress)
        for note in compress_meeting(meeting_dir, cfg["compress_audio"]):
            progress(f"Audio compression: {note}")
    progress("Summarizing with Claude…")
    return finish(meeting_dir, cfg, summarize_meeting(meeting_dir, cfg, prompt_name), progress)


def finish(meeting_dir: Path, cfg: dict, summary: Path, progress=print) -> tuple[Path, Path]:
    """After a summary exists: Word export, then add the title to the folder name."""
    out = make_docx(meeting_dir, cfg, summary, progress) or summary
    new_dir = rename_with_title(meeting_dir, summary)
    return new_dir, new_dir / out.name


def make_docx(meeting_dir: Path, cfg: dict, summary: Path, progress=print) -> Path | None:
    """A failure here never loses the Markdown summary. The Word file is part of the Claude summary
    feature, so it needs auto_summarize on as well (the Settings window greys the option out otherwise)."""
    if not (cfg["export_docx"] and cfg["auto_summarize"]):
        return None
    progress("Creating Word document…")
    try:
        from .docx_export import export_docx

        return export_docx(meeting_dir, summary)
    except Exception as e:
        progress(f"Word export failed (Markdown summary kept): {str(e)[:120]}")
        return None


def clean_title(raw: str, limit: int = 60) -> str:
    t = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", raw)  # characters Finder/Windows/Drive dislike
    t = re.sub(r"\s+", " ", t).strip(" .-")
    return t[:limit].rstrip(" .-")


def rename_with_title(meeting_dir: Path, summary: Path) -> Path:
    """2026-10-07_1030 -> '2026-10-07_1030 - Q4 Marketing Budget Review'. Only the first summary
    names the folder; re-summarizing with another style never renames it again."""
    if not UNTITLED.match(meeting_dir.name):
        return meeting_dir
    m = re.search(r"^#\s+(.+)$", summary.read_text(), flags=re.M)
    title = clean_title(m.group(1)) if m else ""
    if not title:
        return meeting_dir
    target = meeting_dir.with_name(f"{meeting_dir.name} - {title}")
    n = 2
    while target.exists():
        target = meeting_dir.with_name(f"{meeting_dir.name} - {title} ({n})")
        n += 1
    try:
        return Path(shutil.move(str(meeting_dir), str(target)))
    except OSError:
        return meeting_dir
