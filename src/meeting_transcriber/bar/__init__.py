"""The floating control bar: a small always-on-top panel hosting web/overlay/ in a WKWebView.

Same bridge idea as the Settings window (no web server, no open port). The page only sends a fixed set of
requests; the app pushes its state to the page as JSON. The panels never take focus from the app you are in
(Zoom, Meet...), so clicking them does not interrupt a call.

Beside the bar (on the side facing the screen centre) there are small extra panels, most hosting web/popup/:
a tooltip (tips.py), the More menu with its Summary style submenu (menu.py), the Pause / Cancel bar that slides out of
the Stop button (stop_menu.py), the processing panel with one tab per meeting (status.py), the documents list (docs.py)
and the device-changed toast (toast.py). The notes panel (notes.py, web/notes/) is the one panel that takes the
keyboard, so you can type in it; it still does not activate the app, so the meeting window you are in stays in front.

The `host` (the app's BarPresenter, see host.py) supplies the content and does the work.
"""

from .bar import Bar
from .host import BarHost

__all__ = ["Bar", "BarHost"]
