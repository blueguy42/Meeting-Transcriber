"""The More menu."""

from AppKit import NSEvent

from .constants import CLICK_MASK


class MenuMixin:
    """The More menu (with its Summary style submenu)."""

    def _init_menu(self):
        self._monitor = None  # while the menu is open, a click anywhere outside our windows closes it
        self._menu_rects = []  # the visible parts of the menu panel, in its own page coordinates
        self._menu_rows = []  # its rows (x, y, w, h, index), same coordinates
        self._menu_hover = -2  # the row the pointer is over (-1: none)

    def toggle_menu(self, anchor: dict) -> None:
        if self._want["menu"]:
            self.close_menu()
            return
        self.hide_tip()
        self.close_status()  # one popup at a time: opening More closes the processing panel
        self.close_notes()
        self.close_docs()
        self._anchor["menu"] = anchor
        self._want["menu"] = True
        self.menu.render({"kind": "menu", "side": self.side, "items": self.host.overlay_menu(), "fresh": True})
        self.update(self._state)  # the More button stays lit while its menu is open
        # a click anywhere outside our own windows closes the menu
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(CLICK_MASK, lambda e: self.close_menu())
        # The panel is bigger than the menu (room for the submenu), so the empty part must not swallow clicks:
        # it ignores the mouse except while the pointer is over the menu itself.
        self.menu.panel.setIgnoresMouseEvents_(True)
        self._menu_hover = -2
        self._pointer_moved()

    def close_menu(self) -> None:
        was_open = self._want["menu"]
        self._want["menu"] = False
        self.menu.hide()
        if was_open:
            self.update(self._state)
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    def _menu_passthrough(self) -> None:
        """Let clicks through the menu panel everywhere except over the menu (and its submenu)."""
        if not self._want["menu"]:
            return
        p = NSEvent.mouseLocation()
        f = self.menu.panel.frame()
        inside = any(
            f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y
            for x, y, w, h in self._menu_rects
        )
        if bool(self.menu.panel.ignoresMouseEvents()) == inside:  # only touch the window when it actually changes
            self.menu.panel.setIgnoresMouseEvents_(not inside)
        # Which row is the pointer over? Worked out here from the mouse position, because the page does not
        # reliably hear about the mouse in a panel that is not the key window. The page is told only when it changes.
        row = next((i for x, y, w, h, i in self._menu_rows
                    if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)
        if row != self._menu_hover:
            self._menu_hover = row
            self.menu.eval(f"window.__setHoverRow && window.__setHoverRow({row})")
