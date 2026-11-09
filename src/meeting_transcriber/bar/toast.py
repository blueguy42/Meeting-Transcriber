"""Toasts."""

from .constants import FADE_S, TOAST_ONE_LINE
from .popup import Popup


class ToastSlot(Popup):
    """One of the two panels the toasts take turns on."""

    def __init__(self, bar, kind: str):
        self.kind = kind
        self.timers: list = []  # its fade and hide
        super().__init__(bar)

    def build(self) -> None:
        self.surface = self.bar.popup_surface(self.kind, 3, interactive=False)  # above tooltips

    def position(self, x, w, h, f, top, bottom):
        # a one-line toast is level with the microphone button; a longer one keeps its top edge there
        return x, (top + bottom) / 2 + TOAST_ONE_LINE / 2 - h + self.bar.pad

    def cancel_timers(self) -> None:
        for t in self.timers:
            t.invalidate()
        self.timers = []

    def hide(self) -> None:
        self.cancel_timers()
        self.wanted = False
        super().hide()


class Toasts:
    """The device-changed toast: one line beside the bar, fading in and out, one at a time (two panels take turns)."""

    def __init__(self, bar):
        self.bar = bar
        self.slots = [ToastSlot(bar, "toast0"), ToastSlot(bar, "toast1")]
        self.cur = 1  # which slot shows the current toast

    def build(self) -> None:
        for slot in self.slots:
            slot.build()

    def show(self, lines: list, seconds: float = 2.0) -> None:
        """A one-line notice beside the bar, level with the microphone / system audio button (both: between them). Only one
        at a time: a new one fades in as the one showing fades out. It never takes the mouse or the keyboard.
        lines: [(who, device)], who being "Mic", "System" or "Mic and System"; a second line grows the toast downward."""
        bar = self.bar
        if not bar.visible or bar.hidden or bar.dragging:
            return
        items = {i[0]: i for i in bar.items}
        mic = items.get("mic")  # every toast sits in the same spot, level with the microphone button
        if mic is None:
            return
        old = self.slots[self.cur]
        if old.wanted:
            self._fade(old)  # leaves on its own panel while the new one comes in on the other
        self.cur = 1 - self.cur
        slot = self.slots[self.cur]
        self.hide(slot)  # whatever was still fading out there goes at once
        slot.anchor = {"top": mic[2], "bottom": mic[2] + mic[4]}
        slot.wanted = True
        slot.render({"kind": "toast", "side": bar.side, "lines": [{"who": w, "device": d} for w, d in lines], "fresh": True})
        slot.timers = [bar.later(max(seconds - FADE_S, 0.1), lambda: self._fade(slot))]

    def _fade(self, slot: ToastSlot) -> None:
        """Fade the toast on this panel out, then hide the panel."""
        slot.cancel_timers()
        slot.eval("window.__fade && window.__fade()")
        slot.timers = [self.bar.later(FADE_S, lambda: self.hide(slot))]

    def hide(self, slot: ToastSlot | None = None) -> None:
        for s in ((slot,) if slot else self.slots):
            s.hide()
