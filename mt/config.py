"""Settings and paths. User-editable files live in the settings dir, not the repo."""

import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "meetings_dir": "~/Documents/Meetings",
    # Whisper model (downloaded from Hugging Face on first use, ~3 GB).
    "model": "mlx-community/whisper-large-v3-mlx",
    # "auto" detects Indonesian/English per ~60 s chunk (good for code-switching).
    # Or force "id" / "en".
    "language": "auto",
    "allowed_languages": ["id", "en"],
    # Names/terms Whisper should spell right, e.g. ["Budi", "Dina", "Tokopedia"].
    "vocabulary": [],
    "my_label": "Me",
    "others_label": "Others",
    "hotkey_screenshot": "<ctrl>+<alt>+s",
    "hotkey_record": "<ctrl>+<alt>+r",
    # Screenshot size as a percentage of the captured screen's real pixel width (100 = full size).
    "screenshot_scale": 50,
    "auto_summarize": True,
    "default_prompt": "default",
    # Path to the `claude` CLI; empty = auto-detect.
    "claude_path": "",
    "claude_model": "",
    # After transcribing, shrink the audio: "aac" (~30 MB/hour, recommended), "flac" (lossless,
    # ~110 MB/hour) or "off" (keep ~230 MB/hour WAVs). The WAV is deleted only after the
    # compressed file is verified.
    "compress_audio": "aac",
    # Also save each summary as summary.docx (Word), with the used screenshots embedded.
    "export_docx": True,
}


def settings_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "MeetingTranscriber"


def prompts_dir() -> Path:
    return settings_dir() / "prompts"


def defaults() -> dict:
    cfg = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULTS.items()}
    cfg["meetings_dir"] = Path(cfg["meetings_dir"]).expanduser()
    return cfg


def load() -> dict:
    cfg = defaults()
    path = settings_dir() / "config.toml"
    if path.exists():
        user = tomllib.loads(path.read_text())
        cfg.update(user)
    cfg["meetings_dir"] = Path(cfg["meetings_dir"]).expanduser()
    return cfg


def ensure_settings() -> None:
    """First run: copy the bundled prompts and a commented config into the settings dir."""
    sd = settings_dir()
    pd = prompts_dir()
    pd.mkdir(parents=True, exist_ok=True)
    for src in (PROJECT_DIR / "prompts").glob("*.md"):
        dst = pd / src.name
        if not dst.exists():
            shutil.copy(src, dst)
    cfg_path = sd / "config.toml"
    if not cfg_path.exists():
        shutil.copy(PROJECT_DIR / "config.example.toml", cfg_path)


def set_value(key: str, value) -> None:
    set_values({key: value})


def _literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)  # a valid TOML basic string


def set_values(updates: dict) -> None:
    """Write `key = value` pairs into config.toml in one go, editing the file in place so comments
    survive. A key's existing line (active or commented-out) is reused, else the key is added."""
    path = settings_dir() / "config.toml"
    lines = path.read_text().splitlines() if path.exists() else []
    for key, value in updates.items():
        _set_line(lines, key, _literal(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _set_line(lines: list[str], key: str, literal: str) -> None:
    new_line = f"{key} = {literal}"

    # top-level keys must stay above the first real [table] header
    table_at = next((i for i, l in enumerate(lines) if re.match(r"\s*\[", l)), len(lines))
    pat = re.compile(rf"^\s*(#\s*)?{re.escape(key)}\s*=")
    hits = [i for i in range(table_at) if pat.match(lines[i])]
    active = [i for i in hits if not lines[i].lstrip().startswith("#")]
    if hits:
        lines[(active or hits)[0]] = new_line
    else:
        at = table_at
        while at > 0 and not lines[at - 1].strip():  # tuck it under the last setting, not after the blank gap
            at -= 1
        lines.insert(at, new_line)


def reset_settings() -> bool:
    """Restore config.toml to the commented template. The old file is kept as config.toml.bak."""
    path, template = settings_dir() / "config.toml", PROJECT_DIR / "config.example.toml"
    if path.exists():
        if path.read_text() == template.read_text():
            return False
        shutil.copy(path, path.with_suffix(".toml.bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(template, path)
    return True


def list_prompts() -> list[str]:
    return sorted(p.stem for p in prompts_dir().glob("*.md"))


def helper_path() -> Path | None:
    env = os.environ.get("MT_HELPER")
    candidates = [Path(env)] if env else []
    candidates.append(PROJECT_DIR / "sck-audio" / ".build" / "release" / "sck-audio")
    for c in candidates:
        if c.exists():
            return c
    return None


def find_claude(cfg: dict) -> str | None:
    if cfg["claude_path"]:
        return cfg["claude_path"]
    extra = os.pathsep.join(
        [str(Path.home() / ".local/bin"), str(Path.home() / ".claude/local"), "/opt/homebrew/bin", "/usr/local/bin"]
    )
    found = shutil.which("claude", path=extra + os.pathsep + os.environ.get("PATH", ""))
    if found:
        return found
    # The Claude desktop app ships a copy of the CLI.
    app_support = Path.home() / "Library/Application Support/Claude/claude-code"
    builds = sorted(
        app_support.glob("*/*/claude.app/Contents/MacOS/claude"),
        key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parents[4].name.split(".")],
    )
    return str(builds[-1]) if builds else None
