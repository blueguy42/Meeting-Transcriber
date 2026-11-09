"""What every popup beside the bar has in common."""

from AppKit import NSEvent
from Foundation import NSMakeRect

from .constants import POPUP_PAD


class Popup:
    """A panel beside the bar: one web page in its own surface, shown while `wanted`.

    The Bar creates the popups, routes their messages here and asks each one to place itself; a popup reaches the bar and the
    other popups through `self.bar` (to close its neighbours, say, or to show a tooltip)."""

    kind = ""  # the key the popup's messages and placement are routed by

    def __init__(self, bar):
        self.bar = bar
        self.wanted = False  # should be on screen (it shows once its page has said how big it is)
        self.anchor: dict = {}  # the part of the bar it sits beside: {"top", "bottom"} in the bar page's coordinates
        self.surface = None
        self.reset_hit_state()

    def build(self) -> None:
        """Create the panel (again, when the bar is rebuilt)."""
        raise NotImplementedError

    def reset_hit_state(self) -> None:
        """Forget what the page last said about where its buttons are (the panel is new)."""

    # ---- talking to the page
    def render(self, payload: dict) -> None:
        self.surface.render(payload)

    def eval(self, js: str) -> None:
        self.surface.eval(js)

    def hide(self) -> None:
        self.surface.hide()

    @property
    def visible(self) -> bool:
        return self.surface.visible

    def on_message(self, msg) -> None:
        if msg["op"] == "size":
            self.place(float(msg["w"]), float(msg["h"]))

    # ---- placing it
    def place(self, w: float, h: float) -> None:
        """Put the panel beside the bar. `w`, `h`: the size its page asked for."""
        bar = self.bar
        f = bar.frame()
        v = bar.visible_frame()
        top, bottom = f.origin.y + f.size.height - self.anchor.get("top", 0), f.origin.y + f.size.height - self.anchor.get("bottom", 0)
        w, h = self.size(w, h)
        x = f.origin.x - w - bar.gap + POPUP_PAD if bar.side == "right" else f.origin.x + f.size.width + bar.gap - POPUP_PAD
        x, y = self.position(x, w, h, f, top, bottom)
        x = min(max(x, v.origin.x - POPUP_PAD), v.origin.x + v.size.width - w + POPUP_PAD)
        y = min(max(y, v.origin.y - POPUP_PAD), v.origin.y + v.size.height - h + POPUP_PAD)
        self.surface.panel.setFrame_display_(NSMakeRect(x, y, w, h), True)
        self.surface.panel.invalidateShadow()
        if self.wanted:
            self.surface.show()
            self.shown()

    def size(self, w: float, h: float) -> tuple:
        return w, h

    def position(self, x: float, w: float, h: float, bar_frame, top: float, bottom: float) -> tuple:
        """-> (x, y) of the panel's corner. `x` is where a popup beside the bar normally goes; `top`, `bottom`: the anchor in screen coordinates."""
        raise NotImplementedError

    def shown(self) -> None:
        """Called each time the panel has been placed and is on screen."""

    # ---- the pointer
    def hovering(self) -> bool:
        """Is the pointer on one of its buttons (the bar then shows the pointing hand)?"""
        return False

    def pointer(self) -> None:
        """The pointer moved: light up what it is over (only called while the popup is wanted)."""

    def over(self) -> bool:
        p = NSEvent.mouseLocation()
        f = self.surface.panel.frame()
        return f.origin.x <= p.x <= f.origin.x + f.size.width and f.origin.y <= p.y <= f.origin.y + f.size.height

    def row_under_pointer(self, rows: list) -> int:
        """The index of the row (x, y, w, h, index) the pointer is on, in the page's coordinates, or -1."""
        p = NSEvent.mouseLocation()
        f = self.surface.panel.frame()
        return next((i for x, y, w, h, i in rows
                     if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)

    def pointer_inside(self, rects: list) -> bool:
        """Is the pointer inside one of the rectangles (x, y, w, h) of the page?"""
        p = NSEvent.mouseLocation()
        f = self.surface.panel.frame()
        return any(f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y
                   for x, y, w, h in rects)

    def set_hover_row(self, row: int) -> None:
        self.eval(f"window.__setHoverRow && window.__setHoverRow({row})")

    @staticmethod
    def parse_rows(msg) -> list:
        return [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]), int(r["i"])) for r in msg.get("rows", [])]

    @staticmethod
    def parse_rects(msg) -> list:
        return [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])) for r in msg.get("rects", [])]
