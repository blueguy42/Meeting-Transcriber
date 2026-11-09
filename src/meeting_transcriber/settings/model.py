"""The settings, as one typed object. The names are the keys of config.toml."""

import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Settings:
    meetings_dir: Path = Path("~/Documents/Meetings")  # where meetings are saved (a leading ~ is the home folder)
    # Whisper model (downloaded from Hugging Face on first use, ~3 GB).
    model: str = "mlx-community/whisper-large-v3-mlx"
    # "auto" detects Indonesian/English per ~60 s chunk (good for code-switching). Or force "id" / "en".
    language: str = "auto"
    allowed_languages: list = field(default_factory=lambda: ["id", "en"])
    # Names/terms Whisper should spell right, e.g. ["Budi", "Dina", "Tokopedia"].
    vocabulary: list = field(default_factory=list)
    # Tell the other people in a meeting apart ("Others 1", "Others 2"...). 0 = work out how many; or a number.
    diarize: bool = True
    diarize_speakers: int = 0
    my_label: str = "Me"
    others_label: str = "Others"
    # Show the floating bar in screenshots and screen shares (off: it is kept out of both).
    overlay_in_captures: bool = False
    hotkey_screenshot: str = "<ctrl>+<alt>+s"
    hotkey_record: str = "<ctrl>+<alt>+r"
    # Whether each side starts muted when the app opens (muting works any time, also between meetings).
    start_muted_mic: bool = False
    start_muted_system: bool = False
    hotkey_mute_mic: str = "<ctrl>+<alt>+m"
    hotkey_mute_system: str = "<ctrl>+<alt>+n"
    # Meeting notes: the shortcut that opens/closes the notes panel (works in any app), the one that inserts the
    # recording time while typing in it, and whether Claude reads your notes when summarizing.
    hotkey_notes: str = "<ctrl>+<alt>+t"
    hotkey_notes_time: str = "<cmd>+t"
    use_notes: bool = True
    # Meeting documents (the paperclip button): the shortcut that opens the file chooser, whether Claude gets them when
    # summarizing, and whether the chooser's "Read text in images (OCR)" box starts ticked.
    hotkey_documents: str = "<ctrl>+<alt>+d"
    use_documents: bool = True
    documents_ocr: bool = True
    # Screenshot size as a percentage of the captured screen's real pixel width (100 = full size).
    screenshot_scale: int = 50
    auto_summarize: bool = True
    default_prompt: str = "default"
    # Path to the `claude` CLI; empty = auto-detect.
    claude_path: str = ""
    claude_model: str = ""
    # After transcribing, shrink the audio: "aac" (~30 MB/hour, recommended), "flac" (lossless,
    # ~110 MB/hour) or "off" (keep ~230 MB/hour WAVs). The WAV is deleted only after the compressed file is verified.
    compress_audio: str = "aac"
    # Also save every summary (automatic or manual) as summary.docx (Word), with the used screenshots embedded.
    export_docx: bool = True

    def __post_init__(self):
        self.meetings_dir = Path(self.meetings_dir).expanduser()

    @classmethod
    def from_toml(cls, data: dict) -> "Settings":
        """The settings in a parsed config.toml; anything it does not set keeps its default. A key nobody reads is a typo: say so."""
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            log.warning("config.toml has settings this version does not know: %s", ", ".join(unknown))
        return cls(**{k: v for k, v in data.items() if k in known})

    def replace(self, **changes) -> "Settings":
        """A copy with some settings changed."""
        return dataclasses.replace(self, **changes)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
