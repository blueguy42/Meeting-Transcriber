"""The notes panel."""

from Foundation import NSMakePoint

from .constants import POPUP_PAD
from .surfaces import js_literal, screen_of


class NotesMixin:
    """The meeting notes panel: the one panel that takes the keyboard, resizable within limits."""

    def _init_notes(self):
        self._notes_size = None  # the panel's smallest size, as measured by its page (it only reports when that changes)
        self._notes_card = None  # the size you dragged it to, (card width, card height): kept when the bar moves or changes side
        self._notes_focus = False  # shown once the page has measured itself

    # ---- meeting notes
    @property
    def notes_open(self) -> bool:
        return self._want["notes"]

    def toggle_notes(self) -> None:
        self.close_notes() if self._want["notes"] else self.open_notes()

    def open_notes(self) -> None:
        """Show the notes panel beside the notes button and give it the keyboard."""
        if self.hidden:  # the bar is a slim tab: bring the bar back first, the panel sits beside its button
            self.set_hidden(False)
        item = next((i for i in self._bar_items if i[0] == "notes"), None)
        if item is None:
            return
        self.hide_tip()
        self.close_menu()
        self.close_status()
        self.close_docs()
        self._anchor["notes"] = {"top": item[2], "bottom": item[2] + item[4]}
        self._want["notes"] = True
        self._notes_focus = True
        info = self.host.overlay_notes_info()
        self.notes.render({**info, "side": self.side, "elapsed": int(self._state.get("elapsed", 0))})
        if self._notes_size:  # the page only reports its size when it changes, so place it from the last measurement
            self._apply_notes_size()
        self.update(self._state)  # the notes button stays lit while its panel is open

    def close_notes(self) -> None:
        if not self._want["notes"]:
            return
        self.notes.eval("window.flush && window.flush()")  # whatever was typed in the last moment is saved
        self._want["notes"] = False
        self._notes_focus = False
        self.notes.hide()
        self.update(self._state)

    def _notes_limits(self) -> tuple:
        """-> (min card w, min card h, max card w, max card h) for the bar where it is now."""
        pad = POPUP_PAD
        min_w, min_h = self._notes_size[0] - 2 * pad, self._notes_size[1] - 2 * pad
        f = self._bar_frame()
        a = self._anchor.get("notes", {})
        mid = f.origin.y + f.size.height - (a.get("top", 0) + a.get("bottom", 0)) / 2
        bottom = mid - self._notes_size[1] / 2  # the panel's bottom edge, in screen coordinates
        v = screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        top = min(f.origin.y + f.size.height + pad, v.origin.y + v.size.height + pad)  # no higher than the top of the bar
        return min_w, min_h, min_w * self.NOTES_MAX_WIDTH, max(min_h, top - bottom - 2 * pad)

    def _notes_card_size(self) -> tuple:
        min_w, min_h, max_w, max_h = self._notes_limits()
        cw, ch = self._notes_card or (min_w, min_h)
        return min(max(cw, min_w), max_w), min(max(ch, min_h), max_h)

    def _notes_dims(self) -> tuple:
        cw, ch = self._notes_card_size()
        return cw + 2 * POPUP_PAD, ch + 2 * POPUP_PAD

    def _resize_notes(self, msg) -> None:
        phase = msg.get("phase")
        if phase == "start":
            self._notes_base = self._notes_card_size()
        elif phase == "move" and getattr(self, "_notes_base", None) and self._notes_size:
            min_w, min_h, max_w, max_h = self._notes_limits()
            dx, dy = float(msg["dx"]), float(msg["dy"])
            cw = self._notes_base[0] + (dx if self.side == "left" else -dx)  # the bar is on the other side: outwards is away from it
            ch = self._notes_base[1] - dy  # dragging up makes it taller
            self._notes_card = (min(max(cw, min_w), max_w), min(max(ch, min_h), max_h))
            self._apply_notes_size()
        elif phase == "end":
            self._notes_base = None
            self._save()

    def _take_keyboard(self) -> None:
        if self._want["notes"] and not self.notes.panel.isKeyWindow():
            self.notes.panel.makeKeyWindow()
        if self._want["notes"]:
            self.notes.eval("window.__focus && window.__focus()")

    def _apply_notes_size(self) -> None:
        cw, ch = self._notes_card_size()
        self.notes.eval(f"window.__setSize && window.__setSize({cw}, {ch})")
        self._place_popup("notes", *self._notes_dims())

    def reset_notes(self) -> None:
        """A new recording starts with an empty notes panel."""
        self.notes.render({"reset": True})

    def flush_notes(self) -> None:
        self.notes.eval("window.flush && window.flush()")

    def _on_notes(self, msg) -> None:
        op = msg["op"]
        if op == "size":
            self._notes_size = (float(msg["w"]), float(msg["h"]))
            if self._want["notes"]:
                self._apply_notes_size()
        elif op == "notes_resize":
            self._resize_notes(msg)
        elif op == "notes_save":
            err = self.host.overlay_notes_save(str(msg.get("text", "")))
            if err:
                self.notes.eval(f"window.__failed && window.__failed({js_literal(err)})")
        elif op == "notes_close":
            self.close_notes()
