"""The documents list."""

from AppKit import NSEvent
from Foundation import NSMakePoint

from .constants import POPUP_PAD
from .surfaces import screen_of


class DocsMixin:
    """The documents list popup."""

    def _init_docs(self):
        self._docs_rows = []
        self._docs_hover = -2

    # ---- documents list
    def toggle_docs(self) -> None:
        self.close_docs() if self._want["docs"] else self.open_docs()

    def open_docs(self) -> None:
        if self.hidden:
            self.set_hidden(False)
        item = next((i for i in self._bar_items if i[0] == "doclist"), None)
        if item is None:
            return
        self.hide_tip()
        self.close_menu()
        self.close_status()
        self.close_notes()
        self._anchor["docs"] = {"top": item[2], "bottom": item[2] + item[4]}
        self._want["docs"] = True
        self._docs_hover = -2
        self._render_docs()
        self.update(self._state)  # the list button stays lit while its list is open

    def close_docs(self) -> None:
        if not self._want["docs"]:
            return
        self._want["docs"] = False
        self.docs.hide()
        self.update(self._state)

    def _render_docs(self) -> None:
        """The list never grows taller than the bar (its top edge stops level with the bar's top, or the screen's): past that it scrolls."""
        a = self._anchor.get("docs", {})
        f = self._bar_frame()
        v = screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        card_bottom = f.origin.y + f.size.height - a.get("bottom", 0)
        max_h = min(a.get("bottom", 420), v.origin.y + v.size.height - card_bottom - 2 * POPUP_PAD)
        self.docs.render({"kind": "docs", "side": self.side, "maxH": max_h, **self.host.overlay_docs()})

    def _docs_pointer(self) -> None:
        if not self._want["docs"]:
            return
        p = NSEvent.mouseLocation()
        f = self.docs.panel.frame()
        row = next((i for x, y, w, h, i in self._docs_rows
                    if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)
        if row != self._docs_hover:
            self._docs_hover = row
            self.docs.eval(f"window.__setHoverRow && window.__setHoverRow({row})")
