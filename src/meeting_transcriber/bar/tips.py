"""Tooltips."""

from Foundation import NSMakeRect

from .popup import Popup


class TipPopup(Popup):
    """The tooltip beside the bar (its own small panel, above every popup)."""

    kind = "tip"

    def build(self) -> None:
        self.surface = self.bar.popup_surface(self.kind, 2, interactive=False)  # tooltips float above every popup

    def reset_hit_state(self) -> None:
        self.timer = None  # the tooltip waits a beat after the pointer settles, see Bar.pointer_moved
        self.at = None  # (x, top edge, align[, edge to flip to]) of a card the tooltip sits under, in screen coordinates; None: beside the bar

    def show(self, tip_id: str, top: float, bottom: float) -> None:
        """The tooltip of a toolbar button, level with it."""
        if self.bar.dragging:
            return
        tip = self.bar.host.overlay_tip(tip_id)
        if not tip:
            return
        self.at = None
        self.anchor = {"top": top, "bottom": bottom}
        self.wanted = True
        self.render({"kind": "tip", "side": self.bar.side, **tip})

    def show_under(self, at: tuple, text: str, sub: str | None = None) -> None:
        """A tooltip under a card or button of another popup. `at`: see `self.at`."""
        self.at = at
        self.wanted = True
        self.render({"kind": "tip", "side": self.bar.side, "text": text, **({"sub": sub} if sub is not None else {})})

    def hide(self) -> None:
        self.at = None
        self.wanted = False
        super().hide()

    def due(self, hit) -> None:
        self.timer = None
        if self.bar.hover == hit[0]:
            self.show(hit[0], hit[1], hit[2])

    def position(self, x, w, h, f, top, bottom):
        pad = self.bar.pad
        v = self.bar.visible_frame()
        if self.at:
            if self.at[2] == "center":
                x = self.at[0] - w / 2
                y = self.at[1] - h + pad  # a few pixels under the icon
                if y < v.origin.y:  # no room below: above it
                    y = self.at[3] - pad
            else:
                x = self.at[0] - pad if self.at[2] == "left" else self.at[0] - w + pad
                y = self.at[1] - h + pad  # under a card (the Pause / Cancel buttons, a tab)
                if len(self.at) > 3 and y < v.origin.y:  # no room below: above it
                    y = self.at[3] - pad
            return x, y
        return x, (top + bottom) / 2 - h / 2  # level with the middle of its button

    def place(self, w, h):
        if not self.at:
            return super().place(w, h)
        # a tooltip under a card is not pulled back onto the screen (it flips above its card instead)
        bar = self.bar
        f = bar.frame()
        x, y = self.position(0, w, h, f, 0, 0)
        self.surface.panel.setFrame_display_(NSMakeRect(x, y, w, h), True)
        if self.wanted:
            self.surface.show()
