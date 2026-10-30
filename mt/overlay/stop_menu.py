"""The Pause / Cancel bar."""

from AppKit import NSEvent

from .constants import SLIDE_S


class StopMenuMixin:
    """The Pause / Cancel bar attached to the Stop button: it slides out when you rest the pointer on Stop and back when you leave."""

    def _init_stop_menu(self):
        self._ctl_rows = []  # the Pause / Cancel buttons (x, y, w, h, index) in their page's coordinates
        self._ctl_hover = -2
        self._ctl_timer = None  # waits a beat before opening on hover
        self._ctl_leave = None  # and a beat before closing when the pointer has left
        self._ctl_hide = None  # hides the panel once it has slid back
        self._ctl_ready = False  # the bar has slid all the way out: only then do its buttons respond
        self._ctl_ready_timer = None

    # ---- Pause / Cancel, attached to the Stop button while recording
    def _ctl_hover_start(self, hit) -> None:
        if self._want["ctl"]:
            return
        if self._ctl_timer is not None:
            self._ctl_timer.invalidate()
        self._ctl_timer = self._later(0.3, lambda: self._ctl_due(hit))

    def _ctl_due(self, hit) -> None:
        self._ctl_timer = None
        if self._bar_hover == "rec" and self._state.get("recording"):
            self.open_ctl(hit[1], hit[2])

    def open_ctl(self, top: float, bottom: float) -> None:
        if self._ctl_hide is not None:  # it was still sliding back: out again
            self._ctl_hide.invalidate()
            self._ctl_hide = None
        self.hide_tip()
        self._anchor["ctl"] = {"top": top, "bottom": bottom}
        self._want["ctl"] = True
        self._ctl_hover = -2
        self._ctl_ready = False
        if self._ctl_ready_timer is not None:
            self._ctl_ready_timer.invalidate()
        self._ctl_ready_timer = self._later(SLIDE_S + 0.02, self._ctl_slid_out)
        self.ctl.render({"kind": "ctl", "side": self.side, "paused": bool(self._state.get("paused")), "fresh": True})  # fresh: it slides out of the bar
        self._pointer_moved()

    def close_ctl(self) -> None:
        for t in (self._ctl_timer, self._ctl_leave):
            if t is not None:
                t.invalidate()
        self._ctl_timer = self._ctl_leave = None
        if not self._want["ctl"]:
            return
        self._want["ctl"] = False
        self._ctl_ready = False  # (sliding back: the buttons no longer respond)
        if self._ctl_ready_timer is not None:
            self._ctl_ready_timer.invalidate()
            self._ctl_ready_timer = None
        if self._ctl_hover >= 0:
            self.hide_tip()
        self._ctl_hover = -2
        if self.ctl.visible:  # it slides back into the bar, then the panel goes
            self.ctl.eval("window.__slideOut && window.__slideOut()")
            self._ctl_hide = self._later(SLIDE_S + 0.03, self._hide_ctl_now)
        else:
            self.ctl.hide()

    def _ctl_slid_out(self) -> None:
        self._ctl_ready_timer = None
        if self._want["ctl"]:
            self._ctl_ready = True
            self.ctl.eval("window.__ready && window.__ready()")  # the page starts answering clicks and measures the buttons again
            self._ctl_pointer()  # a pointer already resting on a button lights it now

    def _hide_ctl_now(self) -> None:
        if self._ctl_hide is not None:
            self._ctl_hide.invalidate()
            self._ctl_hide = None
        if not self._want["ctl"]:
            self.ctl.hide()

    def _ctl_check_leave(self, hit) -> None:
        """Close a beat after the pointer has left both the Stop button and the Pause / Cancel bar."""
        inside = (hit is not None and hit[0] == "rec") or self._over(self.ctl)
        if inside:
            if self._ctl_leave is not None:
                self._ctl_leave.invalidate()
                self._ctl_leave = None
        elif self._ctl_leave is None:
            self._ctl_leave = self._later(0.25, self.close_ctl)

    def _ctl_pointer(self) -> None:
        if not self._want["ctl"]:
            return
        p = NSEvent.mouseLocation()
        f = self.ctl.panel.frame()
        row = next((i for x, y, w, h, i in self._ctl_rows
                    if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)
        if not self._ctl_ready:
            row = -1  # still sliding: nothing lights up, no hand, no tooltip
        if row != self._ctl_hover:
            self._ctl_hover = row
            self.ctl.eval(f"window.__setHoverRow && window.__setHoverRow({row})")
            if row < 0:
                self.hide_tip()
                return
            first = self._ctl_rows[0]
            paused = bool(self._state.get("paused"))
            text = ("Resume recording" if paused else "Pause recording") if row == first[4] else "Cancel recording…"
            right = self.side == "right"
            edge = f.origin.x + f.size.width - 14 if right else f.origin.x + 14  # the card's outer edge (14: the panel's padding)
            self._tip_at = (edge, f.origin.y + f.size.height - (first[1] + first[3]) - 8, "right" if right else "left")  # under the bar, lined up with its far edge
            self._want["tip"] = True
            self.tip.render({"kind": "tip", "side": self.side, "text": text})
