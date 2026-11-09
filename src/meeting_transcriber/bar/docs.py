"""The documents list."""

from Foundation import NSMakePoint

from .constants import POPUP_PAD
from .popup import Popup
from .surfaces import screen_of


class DocsPopup(Popup):
    """The documents list popup."""

    kind = "docs"

    def build(self) -> None:
        self.surface = self.bar.popup_surface(self.kind)

    def reset_hit_state(self) -> None:
        self.rows = []
        self.hover = -2

    def toggle(self) -> None:
        self.close() if self.wanted else self.open()

    def open(self) -> None:
        bar = self.bar
        if bar.hidden:
            bar.set_hidden(False)
        item = next((i for i in bar.items if i[0] == "doclist"), None)
        if item is None:
            return
        bar.tip.hide()
        bar.menu.close()
        bar.status.close()
        bar.notes.close()
        self.anchor = {"top": item[2], "bottom": item[2] + item[4]}
        self.wanted = True
        self.hover = -2
        self.render_list()
        bar.refresh()  # the list button stays lit while its list is open

    def close(self) -> None:
        if not self.wanted:
            return
        self.wanted = False
        self.hide()
        self.bar.refresh()

    def render_list(self) -> None:
        """The list never grows taller than the bar (its top edge stops level with the bar's top, or the screen's): past that it scrolls."""
        bar = self.bar
        f = bar.frame()
        v = screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        card_bottom = f.origin.y + f.size.height - self.anchor.get("bottom", 0)
        max_h = min(self.anchor.get("bottom", 420), v.origin.y + v.size.height - card_bottom - 2 * POPUP_PAD)
        self.render({"kind": "docs", "side": bar.side, "maxH": max_h, **bar.host.overlay_docs()})

    def position(self, x, w, h, f, top, bottom):
        return x, bottom - self.bar.pad  # the list's bottom edge lines up with its button's

    def hovering(self) -> bool:
        return self.hover >= 0

    def pointer(self) -> None:
        row = self.row_under_pointer(self.rows)
        if row != self.hover:
            self.hover = row
            self.set_hover_row(row)

    def on_message(self, msg) -> None:
        op = msg["op"]
        if op == "rects":
            self.rows = self.parse_rows(msg)
            if self.wanted:
                self.pointer()
        elif op == "docs_delete":
            self.bar.host.overlay_docs_action("delete", str(msg.get("id")))
            self.render_list()
        elif op == "docs_add":
            self.bar.host.overlay_action("docs_add")
        elif op == "docs_close":
            self.close()
        else:
            super().on_message(msg)
