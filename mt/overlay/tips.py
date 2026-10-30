"""Tooltips."""


class TipsMixin:
    """The tooltip beside the bar (its own small panel, above every popup)."""

    def _init_tips(self):
        self._tip_timer = None  # the tooltip waits a beat after the pointer settles, see Overlay._pointer_moved
        self._tip_at = None  # (x, top edge, align) of a card the tooltip sits under, in screen coordinates; None: beside the bar

    def show_tip(self, tip_id: str, top: float, bottom: float) -> None:
        if self._origin0:
            return
        tip = self.host.overlay_tip(tip_id)
        if not tip:
            return
        self._tip_at = None
        self._anchor["tip"] = {"top": top, "bottom": bottom}
        self._want["tip"] = True
        self.tip.render({"kind": "tip", "side": self.side, **tip})

    def hide_tip(self) -> None:
        self._tip_at = None
        self._want["tip"] = False
        self.tip.hide()

    def _tip_due(self, hit) -> None:
        self._tip_timer = None
        if self._bar_hover == hit[0]:
            self.show_tip(hit[0], hit[1], hit[2])
