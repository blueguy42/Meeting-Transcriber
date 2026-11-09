"""The processing panel."""

from .popup import Popup


class StatusPopup(Popup):
    """The processing panel: one tab per meeting (see web/popup/status.js), its hover logic, tooltips and the hint under a tab."""

    kind = "status"

    def build(self) -> None:
        self.surface = self.bar.popup_surface(self.kind)

    def reset_hit_state(self) -> None:
        self.rows = []  # the tabs and their icon buttons (x, y, w, h, index), in the page's coordinates
        self.cards = []  # the panel's card (the window is taller than the card: the rest lets clicks through)
        self.tips = {}  # row index -> tooltip text
        self.tabs = {}  # row index -> when the meeting was recorded, for the rows that are tabs
        self.tab_tip_timer = None  # a tab's hint waits a beat
        self.hover = -2  # the row the pointer is over (-1: none)

    def open(self) -> None:
        bar = self.bar
        bar.notes.close()
        bar.docs.close()
        bar.tip.hide()
        self.anchor = {}
        self.wanted = True
        self.hover = -2
        self.surface.panel.setIgnoresMouseEvents_(True)  # until the pointer is over the card (see pointer)
        self.render_tabs()

    def toggle(self) -> None:
        self.close() if self.wanted else self.open()

    def close(self) -> None:
        self.wanted = False
        self.hide()

    def render_tabs(self) -> None:
        bar = self.bar
        data = bar.host.overlay_status()
        if not data["jobs"]:
            self.close()
            return
        self.render({"kind": "status", "side": bar.side, "maxH": bar.frame().size.height, **data})  # never taller than the toolbar: past that the tabs scroll

    def position(self, x, w, h, f, top, bottom):
        return x, f.origin.y + f.size.height - h + self.bar.pad  # the panel's top edge lines up with the bar's

    def hovering(self) -> bool:
        return self.hover >= 0

    def pointer(self, reapply: bool = False) -> None:
        """reapply: the page was just redrawn, which wiped the highlight: put it back on the row under the pointer (a tooltip
        showing for it is left alone)."""
        if not self.wanted:
            return
        bar = self.bar
        f = self.surface.panel.frame()
        inside = self.pointer_inside(self.cards)
        if bool(self.surface.panel.ignoresMouseEvents()) == inside:  # only touch the window when it actually changes
            self.surface.panel.setIgnoresMouseEvents_(not inside)
        row = self.row_under_pointer(self.rows)
        if row == self.hover and reapply and row >= 0:
            self.set_hover_row(row)
        if row != self.hover:
            self.hover = row
            self.set_hover_row(row)
            if self.tab_tip_timer is not None:
                self.tab_tip_timer.invalidate()
                self.tab_tip_timer = None
            tip = self.tips.get(row)
            if row in self.tabs:  # a tab: after a beat, its full title and when it was recorded
                self.tab_tip_timer = bar.later(0.4, lambda r=row: self._tab_tip_due(r))
                if bar.tip.at is not None:
                    bar.tip.hide()
            elif tip:  # an icon button: its tooltip, centred under it (above it if there is no room)
                x, y, w, h, _i = next(r for r in self.rows if r[4] == row)
                bar.tip.show_under((f.origin.x + x + w / 2, f.origin.y + f.size.height - (y + h) - 4, "center", f.origin.y + f.size.height - y + 4), tip)
            elif bar.tip.at is not None:
                bar.tip.hide()

    def _tab_tip_due(self, row: int) -> None:
        """The hint under a tab: the meeting's whole title (the row cuts it short) and when it was recorded."""
        self.tab_tip_timer = None
        if not self.wanted or self.hover != row or row not in self.tabs:
            return
        f = self.surface.panel.frame()
        x, y, w, h, _i = next(r for r in self.rows if r[4] == row)
        left = f.origin.x + x + 50  # lined up with the title (the arrow, the status icon and their gaps come first)
        self.bar.tip.show_under((left, f.origin.y + f.size.height - (y + h) - 2, "left", f.origin.y + f.size.height - y + 2), self.tips.get(row, ""), self.tabs[row])

    def on_message(self, msg) -> None:
        op = msg["op"]
        if op == "rects":
            self.cards = self.parse_rects(msg)
            self.rows = self.parse_rows(msg)
            self.tips = {int(r["i"]): str(r.get("tip", "")) for r in msg.get("rows", []) if r.get("tip")}  # the icon buttons' tooltips...
            self.tabs = {int(r["i"]): str(r.get("sub", "")) for r in msg.get("rows", []) if r.get("tab")}  # tab rows: their hint carries the time
            self.pointer(reapply=True)
        elif op == "status_close":
            self.close()
            self.bar.host.overlay_status_action("close", None)
        elif op in ("status_summary", "status_folder", "status_record", "status_pause", "status_resume", "status_cancel", "status_again", "status_dismiss"):
            self.bar.host.overlay_status_action(op[len("status_"):], msg.get("id"))
        else:
            super().on_message(msg)
