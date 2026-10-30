"""Toasts."""

from .constants import FADE_S


class ToastMixin:
    """The device-changed toast: one line beside the bar, fading in and out, one at a time (two panels take turns)."""

    def _init_toast(self):
        self._toast_cur = 1  # which toast panel shows the current toast
        self._toast_timers = {"toast0": [], "toast1": []}  # each panel's fade and hide

    def show_toast(self, lines: list, seconds: float = 2.0) -> None:
        """A one-line notice beside the bar, level with the microphone / system audio button (both: between them). Only one
        at a time: a new one fades in as the one showing fades out. It never takes the mouse or the keyboard.
        lines: [(who, device)], who being "Mic", "System" or "Mic and System"; a second line grows the toast downward."""
        if not self.bar.visible or self.hidden or self._origin0:
            return
        items = {i[0]: i for i in self._bar_items}
        mic = items.get("mic")  # every toast sits in the same spot, level with the microphone button
        if mic is None:
            return
        old = f"toast{self._toast_cur}"
        if self._want[old]:
            self._fade_toast(old)  # leaves on its own panel while the new one comes in on the other
        self._toast_cur = 1 - self._toast_cur
        kind = f"toast{self._toast_cur}"
        self.hide_toast(kind)  # whatever was still fading out there goes at once
        self._anchor[kind] = {"top": mic[2], "bottom": mic[2] + mic[4]}
        self._want[kind] = True
        self._popups[kind].render({"kind": "toast", "side": self.side, "lines": [{"who": w, "device": d} for w, d in lines], "fresh": True})
        self._toast_timers[kind] = [self._later(max(seconds - FADE_S, 0.1), lambda: self._fade_toast(kind))]

    def _fade_toast(self, kind: str) -> None:
        """Fade the toast on this panel out, then hide the panel."""
        self._cancel_toast_timers(kind)
        self._popups[kind].eval("window.__fade && window.__fade()")
        self._toast_timers[kind] = [self._later(FADE_S, lambda: self.hide_toast(kind))]

    def hide_toast(self, kind: str | None = None) -> None:
        for k in ((kind,) if kind else ("toast0", "toast1")):
            self._cancel_toast_timers(k)
            self._want[k] = False
            self._popups[k].hide()

    def _cancel_toast_timers(self, kind: str) -> None:
        for t in self._toast_timers[kind]:
            t.invalidate()
        self._toast_timers[kind] = []
