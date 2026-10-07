"""Everything the settings window can do, validated. No UI code here, so it is easy to test.

The window sends untrusted-ish JSON (it is our own page, but input is validated anyway): only known
keys are accepted, with type and range checks, and prompt names/paths cannot escape the prompts folder.
"""

import os
import re
import shutil
from pathlib import Path

from . import config

LANGUAGES = {"auto", "id", "en"}
COMPRESSION = {"aac", "flac", "off"}
MODIFIER = r"<(?:ctrl|alt|shift|cmd)>"
HOTKEY = re.compile(rf"^(?:{MODIFIER}\+)+(?:[a-z0-9]|<f(?:[1-9]|1[0-9]|20)>)$")
MAX_PROMPT_CHARS = 20_000

# keys the window may read/write
EDITABLE = [
    "language", "model", "vocabulary", "my_label", "others_label", "compress_audio", "export_docx",
    "auto_summarize", "default_prompt", "hotkey_record", "hotkey_screenshot", "meetings_dir",
    "screenshot_scale", "claude_path", "claude_model",
]


def builtin_prompts() -> list[str]:
    return sorted(p.stem for p in (config.PROJECT_DIR / "prompts").glob("*.md"))


def _prompt_path(name: str) -> Path:
    if not re.fullmatch(r"[\w\- ]{1,40}", name or ""):
        raise ValueError("Use letters, numbers, spaces, - or _ in prompt names (40 characters max).")
    return config.prompts_dir() / f"{name}.md"


def _public(values: dict) -> dict:
    out = {k: values[k] for k in EDITABLE}
    out["meetings_dir"] = str(values["meetings_dir"])
    return out


def screen_pixels() -> list[int] | None:
    """Real pixel size [width, height] of the main display; screenshot sizes are shown relative to it."""
    try:
        from AppKit import NSScreen

        s = NSScreen.mainScreen()
        f = s.backingScaleFactor()
        return [int(s.frame().size.width * f), int(s.frame().size.height * f)]
    except Exception:
        return None


def snapshot() -> dict:
    cfg = config.load()
    builtin = builtin_prompts()
    prompts = [
        {"name": n, "text": (config.prompts_dir() / f"{n}.md").read_text(), "builtin": n in builtin}
        for n in config.list_prompts()
    ]
    return {
        "values": _public(cfg),
        "defaults": _public(config.defaults()),
        "prompts": prompts,
        "screen_px": screen_pixels(),
        "claude_found": config.find_claude(cfg) or "",
    }


def _check(key: str, v):
    """Return the cleaned value, or raise ValueError with a message for the user."""
    def text(max_len, allow_empty=False):
        if not isinstance(v, str) or (not v.strip() and not allow_empty) or len(v) > max_len or "\n" in v:
            raise ValueError(f"{key}: expected a short single-line text.")
        return v.strip()

    if key == "language":
        if v not in LANGUAGES:
            raise ValueError("language must be auto, id or en.")
        return v
    if key == "compress_audio":
        if v not in COMPRESSION:
            raise ValueError("compress_audio must be aac, flac or off.")
        return v
    if key in ("export_docx", "auto_summarize"):
        if not isinstance(v, bool):
            raise ValueError(f"{key} must be on or off.")
        return v
    if key == "model":
        v = text(120)
        if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", v) and not Path(v).expanduser().exists():
            raise ValueError("model must look like owner/name (a Hugging Face repo) or be a folder.")
        return v
    if key == "vocabulary":
        if not isinstance(v, list) or len(v) > 200 or not all(isinstance(x, str) and 0 < len(x.strip()) <= 60 and "\n" not in x for x in v):
            raise ValueError("vocabulary: a list of up to 200 short words or names.")
        return [x.strip() for x in v]
    if key in ("my_label", "others_label"):
        return text(30)
    if key in ("hotkey_record", "hotkey_screenshot"):
        v = text(60).lower()
        if not HOTKEY.match(v):
            raise ValueError("hotkeys need at least one modifier (ctrl, alt, shift, cmd) plus a key.")
        return v
    if key == "default_prompt":
        if not _prompt_path(v).exists():
            raise ValueError(f'There is no prompt called "{v}".')
        return v
    if key == "meetings_dir":
        p = Path(text(500)).expanduser()
        if not p.is_absolute():
            raise ValueError("The meetings folder must be an absolute path.")
        return str(v).strip()
    if key == "screenshot_scale":
        if isinstance(v, bool) or not isinstance(v, int) or not 10 <= v <= 100:
            raise ValueError("Screenshot size must be a whole percentage between 10 and 100.")
        return v
    if key == "claude_path":
        v = text(500, allow_empty=True)
        if v and not os.access(Path(v).expanduser(), os.X_OK):
            raise ValueError("That Claude path is not an executable file.")
        return v
    if key == "claude_model":
        return text(60, allow_empty=True)
    raise ValueError(f"Unknown setting: {key}")


def save_values(values: dict) -> dict:
    """Validate everything first, then write once: all or nothing."""
    unknown = set(values) - set(EDITABLE)
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}")
    clean = {k: _check(k, v) for k, v in values.items()}
    if "hotkey_record" in clean or "hotkey_screenshot" in clean:
        current = config.load()  # a single changed hotkey must not collide with the one not being saved
        merged = {k: clean.get(k, current[k]) for k in ("hotkey_record", "hotkey_screenshot")}
        if merged["hotkey_record"] == merged["hotkey_screenshot"]:
            raise ValueError("The two hotkeys must be different.")
    if "my_label" in clean or "others_label" in clean:
        current = config.load()  # equal labels would merge both speakers' transcripts into one
        if clean.get("my_label", current["my_label"]) == clean.get("others_label", current["others_label"]):
            raise ValueError("The two speaker labels must be different.")
    config.set_values(clean)
    return snapshot()


def reset_settings() -> dict:
    config.reset_settings()
    return snapshot()


# ---- prompts
def save_prompt(name: str, text: str) -> dict:
    p = _prompt_path(name)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A prompt can't be empty.")
    if len(text) > MAX_PROMPT_CHARS:
        raise ValueError(f"A prompt can be at most {MAX_PROMPT_CHARS} characters.")
    if not p.exists():
        raise ValueError(f'There is no prompt called "{name}".')
    p.write_text(text if text.endswith("\n") else text + "\n")
    return snapshot()


def new_prompt(name: str, text: str | None = None) -> dict:
    p = _prompt_path(name.strip())
    if p.exists():
        raise ValueError(f'A prompt called "{name}" already exists.')
    base = config.prompts_dir() / "default.md"
    p.write_text(text if text else (base.read_text() if base.exists() else (config.PROJECT_DIR / "prompts" / "default.md").read_text()))
    return snapshot()


def delete_prompt(name: str) -> dict:
    if name in builtin_prompts():
        raise ValueError("Built-in prompts can't be deleted (use Reset to restore them).")
    p = _prompt_path(name)
    if not p.exists():
        raise ValueError(f'There is no prompt called "{name}".')
    if config.load()["default_prompt"] == name:
        config.set_values({"default_prompt": "default"})
    p.unlink()
    return snapshot()


def rename_prompt(old: str, new: str) -> dict:
    if old in builtin_prompts():
        raise ValueError("Built-in prompts can't be renamed.")
    src, dst = _prompt_path(old), _prompt_path(new.strip())
    if not src.exists():
        raise ValueError(f'There is no prompt called "{old}".')
    if dst.exists():
        raise ValueError(f'A prompt called "{new}" already exists.')
    src.rename(dst)
    if config.load()["default_prompt"] == old:
        config.set_values({"default_prompt": dst.stem})
    return snapshot()


def reset_prompt(name: str) -> dict:
    """Restore one built-in prompt (the edited version is kept as .md.bak)."""
    if name not in builtin_prompts():
        raise ValueError("Only built-in prompts have a default to go back to.")
    dst = _prompt_path(name)
    src = config.PROJECT_DIR / "prompts" / f"{name}.md"
    if dst.exists() and dst.read_text() != src.read_text():
        shutil.copy(dst, dst.with_suffix(".md.bak"))
    shutil.copy(src, dst)
    return snapshot()


# ---- message dispatch used by the settings window (UI-only operations live in settings_window.py)
OPS = {
    "load": lambda a: snapshot(),
    "save_values": lambda a: save_values(a["values"]),
    "reset_settings": lambda a: reset_settings(),
    "save_prompt": lambda a: save_prompt(a["name"], a["text"]),
    "new_prompt": lambda a: new_prompt(a["name"], a.get("text")),
    "delete_prompt": lambda a: delete_prompt(a["name"]),
    "rename_prompt": lambda a: rename_prompt(a["old"], a["new"]),
    "reset_prompt": lambda a: reset_prompt(a["name"]),
}
CHANGES = {"save_values", "reset_settings", "save_prompt", "new_prompt", "delete_prompt", "rename_prompt", "reset_prompt"}
