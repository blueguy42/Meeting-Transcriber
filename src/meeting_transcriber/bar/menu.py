"""The More menu."""

from AppKit import NSEvent

from .constants import CLICK_MASK
from .popup import Popup


class MenuPopup(Popup):
    """The More menu (with its Summary style submenu)."""

    kind = "menu"

    def build(self) -> None:
        self.surface = self.bar.popup_surface(self.kind)

    def reset_hit_state(self) -> None:
        self.monitor = None  # while the menu is open, a click anywhere outside our windows closes it
        self.rects = []  # the visible parts of the menu panel, in its own page coordinates
        self.rows = []  # its rows (x, y, w, h, index), same coordinates
        self.hover = -2  # the row the pointer is over (-1: none)

    def toggle(self, anchor: dict) -> None:
        bar = self.bar
        if self.wanted:
            self.close()
            return
        bar.tip.hide()
        bar.status.close()  # one popup at a time: opening More closes the processing panel
        bar.notes.close()
        bar.docs.close()
        self.anchor = anchor
        self.wanted = True
        self.render({"kind": "menu", "side": bar.side, "items": bar.host.overlay_menu(), "fresh": True})
        bar.refresh()  # the More button stays lit while its menu is open
        # a click anywhere outside our own windows closes the menu
        self.monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(CLICK_MASK, lambda e: self.close())
        # The panel is bigger than the menu (room for the submenu), so the empty part must not swallow clicks:
        # it ignores the mouse except while the pointer is over the menu itself.
        self.surface.panel.setIgnoresMouseEvents_(True)
        self.hover = -2
        bar.pointer_moved()

    def close(self) -> None:
        was_open = self.wanted
        self.wanted = False
        self.hide()
        if was_open:
            self.bar.refresh()
        if self.monitor is not None:
            NSEvent.removeMonitor_(self.monitor)
            self.monitor = None

    def position(self, x, w, h, f, top, bottom):
        return x, bottom - self.bar.pad  # the menu's bottom edge lines up with its button's

    def hovering(self) -> bool:
        return self.hover >= 0

    def pointer(self) -> None:
        """Let clicks through the menu panel everywhere except over the menu (and its submenu)."""
        panel = self.surface.panel
        inside = self.pointer_inside(self.rects)
        if bool(panel.ignoresMouseEvents()) == inside:  # only touch the window when it actually changes
            panel.setIgnoresMouseEvents_(not inside)
        # Which row is the pointer over? Worked out here from the mouse position, because the page does not
        # reliably hear about the mouse in a panel that is not the key window. The page is told only when it changes.
        row = self.row_under_pointer(self.rows)
        if row != self.hover:
            self.hover = row
            self.set_hover_row(row)

    def on_message(self, msg) -> None:
        op = msg["op"]
        if op == "rects":
            self.rects = self.parse_rects(msg)
            self.rows = self.parse_rows(msg)
            if self.wanted:
                self.pointer()
        elif op == "menu_click":
            self.close()
            self.bar.host.overlay_menu_click(str(msg["id"]))
        else:
            super().on_message(msg)
