"""The Pause / Cancel bar."""

from .constants import SLIDE_S
from .popup import Popup


class StopMenuPopup(Popup):
    """The Pause / Cancel bar attached to the Stop button: it slides out when you rest the pointer on Stop and back when you leave."""

    kind = "ctl"

    def build(self) -> None:
        self.surface = self.bar.popup_surface(self.kind)  # Pause / Cancel, attached to the Stop button

    def reset_hit_state(self) -> None:
        self.rows = []  # the Pause / Cancel buttons (x, y, w, h, index) in their page's coordinates
        self.hover = -2
        self.timer = None  # waits a beat before opening on hover
        self.leave_timer = None  # and a beat before closing when the pointer has left
        self.hide_timer = None  # hides the panel once it has slid back
        self.ready = False  # the bar has slid all the way out: only then do its buttons respond
        self.ready_timer = None

    # ---- Pause / Cancel, attached to the Stop button while recording
    def hover_start(self, hit) -> None:
        if self.wanted:
            return
        if self.timer is not None:
            self.timer.invalidate()
        self.timer = self.bar.later(0.3, lambda: self._due(hit))

    def _due(self, hit) -> None:
        self.timer = None
        if self.bar.hover == "rec" and self.bar.state.get("recording"):
            self.open(hit[1], hit[2])

    def open(self, top: float, bottom: float) -> None:
        bar = self.bar
        if self.hide_timer is not None:  # it was still sliding back: out again
            self.hide_timer.invalidate()
            self.hide_timer = None
        bar.tip.hide()
        self.anchor = {"top": top, "bottom": bottom}
        self.wanted = True
        self.hover = -2
        self.ready = False
        if self.ready_timer is not None:
            self.ready_timer.invalidate()
        self.ready_timer = bar.later(SLIDE_S + 0.02, self._slid_out)
        self.render({"kind": "ctl", "side": bar.side, "paused": bool(bar.state.get("paused")), "fresh": True})  # fresh: it slides out of the bar
        bar.pointer_moved()

    def close(self) -> None:
        for t in (self.timer, self.leave_timer):
            if t is not None:
                t.invalidate()
        self.timer = self.leave_timer = None
        if not self.wanted:
            return
        self.wanted = False
        self.ready = False  # (sliding back: the buttons no longer respond)
        if self.ready_timer is not None:
            self.ready_timer.invalidate()
            self.ready_timer = None
        if self.hover >= 0:
            self.bar.tip.hide()
        self.hover = -2
        if self.visible:  # it slides back into the bar, then the panel goes
            self.eval("window.__slideOut && window.__slideOut()")
            self.hide_timer = self.bar.later(SLIDE_S + 0.03, self.hide_now)
        else:
            self.hide()

    def _slid_out(self) -> None:
        self.ready_timer = None
        if self.wanted:
            self.ready = True
            self.eval("window.__ready && window.__ready()")  # the page starts answering clicks and measures the buttons again
            self.pointer()  # a pointer already resting on a button lights it now

    def hide_now(self) -> None:
        if self.hide_timer is not None:
            self.hide_timer.invalidate()
            self.hide_timer = None
        if not self.wanted:
            self.hide()

    def check_leave(self, hit) -> None:
        """Close a beat after the pointer has left both the Stop button and the Pause / Cancel bar."""
        inside = (hit is not None and hit[0] == "rec") or self.over()
        if inside:
            if self.leave_timer is not None:
                self.leave_timer.invalidate()
                self.leave_timer = None
        elif self.leave_timer is None:
            self.leave_timer = self.bar.later(0.25, self.close)

    def position(self, x, w, h, f, top, bottom):
        pad = self.bar.pad
        y = (top + bottom) / 2 - h / 2  # level with the Stop button
        x = f.origin.x - w + pad if self.bar.side == "right" else f.origin.x + f.size.width - pad  # touching the bar
        return x, y

    def hovering(self) -> bool:
        return self.hover >= 0

    def pointer(self) -> None:
        if not self.wanted:
            return
        bar = self.bar
        f = self.surface.panel.frame()
        row = self.row_under_pointer(self.rows)
        if not self.ready:
            row = -1  # still sliding: nothing lights up, no hand, no tooltip
        if row != self.hover:
            self.hover = row
            self.set_hover_row(row)
            if row < 0:
                bar.tip.hide()
                return
            first = self.rows[0]
            paused = bool(bar.state.get("paused"))
            text = ("Resume recording" if paused else "Pause recording") if row == first[4] else "Cancel recording…"
            right = bar.side == "right"
            edge = f.origin.x + f.size.width - 14 if right else f.origin.x + 14  # the card's outer edge (14: the panel's padding)
            bar.tip.show_under((edge, f.origin.y + f.size.height - (first[1] + first[3]) - 8, "right" if right else "left"), text)  # under the bar, lined up with its far edge

    def on_message(self, msg) -> None:
        op = msg["op"]
        if op == "rects":
            self.rows = self.parse_rows(msg)
            self.pointer()
        elif op in ("ctl_pause", "ctl_cancel"):
            self.close()
            self.bar.host.overlay_action("pause" if op == "ctl_pause" else "cancel_rec")
        else:
            super().on_message(msg)
