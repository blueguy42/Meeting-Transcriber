"""The floating control bar: a small always-on-top panel hosting mt/ui/overlay.html in a WKWebView.

Same bridge idea as the Settings window (no web server, no open port). The page only sends a fixed set of
requests; the app pushes its state to the page as JSON. The panels never take focus from the app you are in
(Zoom, Meet...), so clicking them does not interrupt a call.

Beside the bar (on the side facing the screen centre) there are small extra panels, most hosting mt/ui/popup.html:
a tooltip (tips.py), the More menu with its Summary style submenu (menu.py), the Pause / Cancel bar that slides out of
the Stop button (stop_menu.py), the processing panel with one tab per meeting (status.py), the documents list (docs.py)
and the device-changed toast (toast.py). The notes panel (notes.py, mt/ui/notes.html) is the one panel that takes the
keyboard, so you can type in it; it still does not activate the app, so the meeting window you are in stays in front.

The `host` (the App in app.py, see overlay_host.py) supplies the content and does the work:
    host.overlay_action(op)            "toggle" | "shot" | "notes" | "docs_add" | "mute_mic" | "mute_system" | "pause" |
                                       "cancel_rec" | "perm" | "quit"
    host.overlay_tip(id) -> {"text", "hotkey"} | None
    host.overlay_menu() -> [{"id", "label", "hint"?, "sub"?: [{"id", "label", "checked"}]} | {"divider": True}]
    host.overlay_menu_click(id)
    host.overlay_status() -> {"jobs": [...], "canRecord": bool}
    host.overlay_status_action(act, job_id)   "summary" | "folder" | "record" | "close" | "pause" | "resume" | "cancel" |
                                              "again" | "dismiss"
    host.overlay_docs() -> {"docs": [...]}                  the documents list (see overlay_host.py)
    host.overlay_docs_action("delete", id)
    host.overlay_notes_info() -> {"timeKey", "timeLabel"}   how the notes panel inserts the time
    host.overlay_notes_save(text) -> None | an error message
"""

from .overlay import Overlay

__all__ = ["Overlay"]
