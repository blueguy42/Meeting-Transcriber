"""The two macOS permissions the app needs, and how to ask for them.

  - Microphone                       -> your voice
  - Screen & System Audio Recording  -> other participants' audio, and screenshots

macOS shows each system prompt only once; after that the user has to flip the switch in System
Settings, so this module also opens the right Settings pane.
"""

import ctypes
import json
import subprocess

from . import config

PANES = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
}
LABELS = {"microphone": "Microphone", "screen": "Screen & System Audio Recording"}

_cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
_cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool


def mic_status() -> str:
    """'granted', 'denied', 'undetermined' (never asked) or 'unknown'. Never shows a prompt."""
    try:
        import AVFoundation as AV

        code = int(AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio))
    except Exception:
        return "unknown"
    return {0: "undetermined", 1: "denied", 2: "denied", 3: "granted"}.get(code, "unknown")


def request_mic(done) -> None:
    """Show the system microphone prompt; done(granted: bool) is called later, on a background thread."""
    import AVFoundation as AV

    AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeAudio, lambda ok: done(bool(ok)))


def screen_granted() -> bool:
    return bool(_cg.CGPreflightScreenCaptureAccess())


def request_screen() -> None:
    """Shows the system prompt the first time (and adds the app to the Settings list); later calls do nothing."""
    _cg.CGRequestScreenCaptureAccess()


def missing() -> list[str]:
    """Permissions that are definitely not granted ('microphone' / 'screen')."""
    out = []
    if mic_status() in ("denied", "undetermined"):
        out.append("microphone")
    if not screen_granted():
        out.append("screen")
    return out


def open_settings(which: str) -> None:
    subprocess.run(["open", PANES[which]])


# ---- remembering that the welcome step has been shown
def _state_path():
    return config.settings_dir() / "state.json"


def welcomed() -> bool:
    try:
        return bool(json.loads(_state_path().read_text()).get("welcomed"))
    except Exception:
        return False


def mark_welcomed() -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"welcomed": True}))
