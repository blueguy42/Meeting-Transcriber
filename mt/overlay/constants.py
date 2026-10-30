"""Sizes, window levels, delays and event masks shared by the floating bar and its popups."""

from pathlib import Path


UI = Path(__file__).resolve().parent.parent / "ui"
NONACTIVATING_PANEL = 1 << 7
STATUS_LEVEL = 25  # NSStatusWindowLevel: above normal windows and the Dock
MARGIN = 24  # gap between the bar and the screen edge
GAP = 8  # gap between the bar and a popup
TOAST_ONE_LINE = 52.0  # a one-line toast's card height
SLIDE_S = 0.18  # the Pause / Cancel bar slides out of, and back into, the toolbar this long
FADE_S = 0.25  # a toast fades in and out this long
POPUP_PAD = 14.0  # transparent border around a popup's cards (room for their CSS shadow), see popup.html
CLICK_MASK = (1 << 1) | (1 << 3) | (1 << 25)  # left, right, other mouse down
MOVE_MASK = 1 << 5  # mouse moved
# NSTrackingMouseEnteredAndExited | MouseMoved | ActiveAlways | InVisibleRect
TRACK_ALWAYS = 0x01 | 0x02 | 0x04 | 0x80 | 0x200  # (0x04: cursorUpdate)
OPS = {"toggle", "shot", "notes", "docs_add", "mute_mic", "mute_system", "perm", "quit", "pause", "cancel_rec"}
