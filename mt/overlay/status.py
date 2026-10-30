"""The processing panel."""

from AppKit import NSEvent


class StatusMixin:
    """The processing panel: one tab per meeting (see popup.html), its hover logic, tooltips and the hint under a tab."""

    def _init_status(self):
        self._status_rows = []  # the tabs and their icon buttons (x, y, w, h, index), in the page's coordinates
        self._status_cards = []  # the panel's card (the window is taller than the card: the rest lets clicks through)
        self._status_tips = {}  # row index -> tooltip text
        self._status_tabs = {}  # row index -> when the meeting was recorded, for the rows that are tabs
        self._tab_tip_timer = None  # a tab's hint waits a beat
        self._status_hover = -2  # the row the pointer is over (-1: none)

    def _status_pointer(self, reapply: bool = False) -> None:
        """reapply: the page was just redrawn, which wiped the highlight: put it back on the row under the pointer (a tooltip
        showing for it is left alone)."""
        if not self._want["status"]:
            return
        p = NSEvent.mouseLocation()
        f = self.status.panel.frame()
        inside = any(f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y
                     for x, y, w, h in self._status_cards)
        if bool(self.status.panel.ignoresMouseEvents()) == inside:  # only touch the window when it actually changes
            self.status.panel.setIgnoresMouseEvents_(not inside)
        row = next((i for x, y, w, h, i in self._status_rows
                    if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)
        if row == self._status_hover and reapply and row >= 0:
            self.status.eval(f"window.__setHoverRow && window.__setHoverRow({row})")
        if row != self._status_hover:
            self._status_hover = row
            self.status.eval(f"window.__setHoverRow && window.__setHoverRow({row})")
            if self._tab_tip_timer is not None:
                self._tab_tip_timer.invalidate()
                self._tab_tip_timer = None
            tip = self._status_tips.get(row)
            if row in self._status_tabs:  # a tab: after a beat, its full title and when it was recorded
                self._tab_tip_timer = self._later(0.4, lambda r=row: self._tab_tip_due(r))
                if self._tip_at is not None:
                    self.hide_tip()
            elif tip:  # an icon button: its tooltip, centred under it (above it if there is no room)
                x, y, w, h, _i = next(r for r in self._status_rows if r[4] == row)
                self._tip_at = (f.origin.x + x + w / 2, f.origin.y + f.size.height - (y + h) - 4, "center", f.origin.y + f.size.height - y + 4)
                self._want["tip"] = True
                self.tip.render({"kind": "tip", "side": self.side, "text": tip})
            elif self._tip_at is not None:
                self.hide_tip()

    def _tab_tip_due(self, row: int) -> None:
        """The hint under a tab: the meeting's whole title (the row cuts it short) and when it was recorded."""
        self._tab_tip_timer = None
        if not self._want["status"] or self._status_hover != row or row not in self._status_tabs:
            return
        f = self.status.panel.frame()
        x, y, w, h, _i = next(r for r in self._status_rows if r[4] == row)
        left = f.origin.x + x + 50  # lined up with the title (the arrow, the status icon and their gaps come first)
        self._tip_at = (left, f.origin.y + f.size.height - (y + h) - 2, "left", f.origin.y + f.size.height - y + 2)
        self._want["tip"] = True
        self.tip.render({"kind": "tip", "side": self.side, "text": self._status_tips.get(row, ""), "sub": self._status_tabs[row]})

    def open_status(self) -> None:
        self.close_notes()
        self.close_docs()
        self.hide_tip()
        self._anchor["status"] = {}
        self._want["status"] = True
        self._status_hover = -2
        self.status.panel.setIgnoresMouseEvents_(True)  # until the pointer is over the card (see _status_pointer)
        self._render_status()

    def toggle_status(self) -> None:
        self.close_status() if self._want["status"] else self.open_status()

    def close_status(self) -> None:
        self._want["status"] = False
        self.status.hide()

    def _render_status(self) -> None:
        data = self.host.overlay_status()
        if not data["jobs"]:
            self.close_status()
            return
        self.status.render({"kind": "status", "side": self.side, "maxH": self._bar_frame().size.height, **data})  # never taller than the toolbar: past that the tabs scroll
