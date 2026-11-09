"""The notes panel."""

from Foundation import NSMakePoint, NSRunLoop, NSRunLoopCommonModes, NSTimer

from .constants import POPUP_PAD, STATUS_LEVEL
from .popup import Popup
from .surfaces import js_literal, screen_of, Surface


class NotesPopup(Popup):
    """The meeting notes panel: the one panel that takes the keyboard, resizable within limits.

    It is as small as its page says at least, up to half as wide again, and up to the top of the bar tall. Its bottom edge
    stays where it is; the grip is in the top corner away from the bar."""

    kind = "notes"
    MAX_WIDTH = 1.5

    def build(self) -> None:
        self.surface = Surface("notes", self.on_message, level=STATUS_LEVEL + 1, shadow=False, capturable=self.bar.capturable, can_key=True)

    def reset_hit_state(self) -> None:
        self.min_size = None  # the panel's smallest size, as measured by its page (it only reports when that changes)
        self.focus = False  # shown once the page has measured itself
        self.base = None  # the card size when a resize started
        if not hasattr(self, "card"):
            self.card = None  # the size you dragged it to, (card width, card height): kept when the bar moves or changes side

    # ---- meeting notes
    def toggle(self) -> None:
        self.close() if self.wanted else self.open()

    def open(self) -> None:
        """Show the notes panel beside the notes button and give it the keyboard."""
        bar = self.bar
        if bar.hidden:  # the bar is a slim tab: bring the bar back first, the panel sits beside its button
            bar.set_hidden(False)
        item = next((i for i in bar.items if i[0] == "notes"), None)
        if item is None:
            return
        bar.tip.hide()
        bar.menu.close()
        bar.status.close()
        bar.docs.close()
        self.anchor = {"top": item[2], "bottom": item[2] + item[4]}
        self.wanted = True
        self.focus = True
        info = bar.host.overlay_notes_info()
        self.render({**info, "side": bar.side, "elapsed": int(bar.state.get("elapsed", 0))})
        if self.min_size:  # the page only reports its size when it changes, so place it from the last measurement
            self.apply_size()
        bar.refresh()  # the notes button stays lit while its panel is open

    def close(self) -> None:
        if not self.wanted:
            return
        self.eval("window.flush && window.flush()")  # whatever was typed in the last moment is saved
        self.wanted = False
        self.focus = False
        self.hide()
        self.bar.refresh()

    def limits(self) -> tuple:
        """-> (min card w, min card h, max card w, max card h) for the bar where it is now."""
        pad = POPUP_PAD
        min_w, min_h = self.min_size[0] - 2 * pad, self.min_size[1] - 2 * pad
        f = self.bar.frame()
        a = self.anchor
        mid = f.origin.y + f.size.height - (a.get("top", 0) + a.get("bottom", 0)) / 2
        bottom = mid - self.min_size[1] / 2  # the panel's bottom edge, in screen coordinates
        v = screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        top = min(f.origin.y + f.size.height + pad, v.origin.y + v.size.height + pad)  # no higher than the top of the bar
        return min_w, min_h, min_w * self.MAX_WIDTH, max(min_h, top - bottom - 2 * pad)

    def card_size(self) -> tuple:
        min_w, min_h, max_w, max_h = self.limits()
        cw, ch = self.card or (min_w, min_h)
        return min(max(cw, min_w), max_w), min(max(ch, min_h), max_h)

    def dims(self) -> tuple:
        cw, ch = self.card_size()
        return cw + 2 * POPUP_PAD, ch + 2 * POPUP_PAD

    def _resize(self, msg) -> None:
        phase = msg.get("phase")
        if phase == "start":
            self.base = self.card_size()
        elif phase == "move" and self.base and self.min_size:
            min_w, min_h, max_w, max_h = self.limits()
            dx, dy = float(msg["dx"]), float(msg["dy"])
            cw = self.base[0] + (dx if self.bar.side == "left" else -dx)  # the bar is on the other side: outwards is away from it
            ch = self.base[1] - dy  # dragging up makes it taller
            self.card = (min(max(cw, min_w), max_w), min(max(ch, min_h), max_h))
            self.apply_size()
        elif phase == "end":
            self.base = None
            self.bar.save()

    def _take_keyboard(self) -> None:
        if self.wanted and not self.surface.panel.isKeyWindow():
            self.surface.panel.makeKeyWindow()
        if self.wanted:
            self.eval("window.__focus && window.__focus()")

    def apply_size(self) -> None:
        cw, ch = self.card_size()
        self.eval(f"window.__setSize && window.__setSize({cw}, {ch})")
        self.place(*self.dims())

    # ---- placing it
    def size(self, w: float, h: float) -> tuple:
        return self.dims() if self.min_size else (w, h)

    def position(self, x, w, h, f, top, bottom):
        # bottom edge: as if it were the smallest size, it grows upwards
        return x, (top + bottom) / 2 - self.min_size[1] / 2 if self.min_size else (top + bottom) / 2 - h / 2

    def shown(self) -> None:
        if self.focus:  # opened just now: the keyboard goes to the notes
            self.focus = False
            self._take_keyboard()
            t = NSTimer.timerWithTimeInterval_repeats_block_(0.15, False, lambda _t: self._take_keyboard())  # once more, in case the first try came too early
            NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)

    def reset(self) -> None:
        """A new recording starts with an empty notes panel."""
        self.render({"reset": True})

    def on_message(self, msg) -> None:
        op = msg["op"]
        if op == "size":
            self.min_size = (float(msg["w"]), float(msg["h"]))
            if self.wanted:
                self.apply_size()
        elif op == "notes_resize":
            self._resize(msg)
        elif op == "notes_save":
            err = self.bar.host.overlay_notes_save(str(msg.get("text", "")))
            if err:
                self.eval(f"window.__failed && window.__failed({js_literal(err)})")
        elif op == "notes_close":
            self.close()
