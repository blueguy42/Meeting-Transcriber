"""The floating control bar."""

import json
import logging
from pathlib import Path

from AppKit import NSCursor, NSEvent, NSScreen
from Foundation import NSMakePoint, NSMakeRect, NSRunLoop, NSRunLoopCommonModes, NSTimer

from ..settings import config
from .constants import GAP, MARGIN, MOVE_MASK, OPS, POPUP_PAD, STATUS_LEVEL
from .docs import DocsPopup
from .menu import MenuPopup
from .notes import NotesPopup
from .status import StatusPopup
from .stop_menu import StopMenuPopup
from .surfaces import allow_cursor_in_background, find_screen, Ghost, js_literal, screen_id, screen_of, Surface
from .tips import TipPopup
from .toast import Toasts

log = logging.getLogger(__name__)


class Bar:
    """The bar itself (placement, dragging, visibility, which part of it the pointer is on). Each popup beside it is a class of
    its own: tooltips, toast, More menu, the Pause / Cancel bar, the processing panel, notes and documents."""

    gap = GAP
    pad = POPUP_PAD

    def __init__(self, host):
        self.host = host
        allow_cursor_in_background()  # the pointing hand must also show while another app is in front
        self.side = "right"  # which screen edge the bar is on ("left" or "right")
        self.hidden = False  # collapsed to the slim edge tab
        self.top = None  # distance from the top of the usable screen area to the top of the bar
        self.screen_id = None
        self._size = (64, 330)
        self._bar_h = 328.0  # the full bar's height: the hidden tab sits at its vertical middle
        self._origin0 = None  # where the bar was when a drag started (None: not dragging)
        self.state = {}  # what the app last told the bar
        self.items = []  # the toolbar buttons (id, x, y, w, h) in the bar page's coordinates
        self.hover = None  # the button the pointer is on
        self._poll = None  # a 60 Hz look at the pointer while it is over one of our panels
        self._hand = False  # the pointing-hand cursor is showing (over a button in the bar or one of its popups)
        self.tip = TipPopup(self)
        self.menu = MenuPopup(self)
        self.status = StatusPopup(self)
        self.docs = DocsPopup(self)
        self.ctl = StopMenuPopup(self)  # Pause / Cancel, attached to the Stop button
        self.toasts = Toasts(self)  # two panels, used in turn
        self.notes = NotesPopup(self)
        self.popups = {p.kind: p for p in (self.tip, self.menu, self.status, self.docs, self.ctl, self.notes, *self.toasts.slots)}  # by the key their messages come with
        self._load()
        self.capturable = bool(host.overlay_in_captures())  # shown in screenshots and screen shares?
        self._build_surfaces()
        # Mouse-moved events reach a tooltip-style panel late or in clumps, but the app-wide monitors get them
        # promptly (hover had no lag while these were running for the open More menu), so they run all the time;
        # pointer_moved leaves at once when the pointer is nowhere near us.
        self._move_monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(MOVE_MASK, lambda e: self.pointer_moved()),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(MOVE_MASK, lambda e: (self.pointer_moved(), e)[1]),
        ]

    # ---- what the popups use
    @property
    def dragging(self) -> bool:
        return bool(self._origin0)

    def frame(self):
        """The bar's panel, in screen coordinates."""
        return self.surface.panel.frame()

    def visible_frame(self):
        """The usable area of the screen the bar is on."""
        f = self.frame()
        return screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()

    @staticmethod
    def later(seconds: float, fn):
        t = NSTimer.timerWithTimeInterval_repeats_block_(seconds, False, lambda _t: fn())
        NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)
        return t

    def refresh(self) -> None:
        """Draw the bar again from what the app last told it (a button lights or goes out)."""
        self.update(self.state)

    def popup_surface(self, kind: str, level_above_bar: int = 1, interactive: bool = True, page: str = "popup") -> Surface:
        """One popup panel. Interactive ones hear the pointer (we work out what is under it ourselves); the others let clicks through."""
        cap = self.capturable
        on_message = lambda m: self.popups[kind].on_message(m)  # noqa: E731
        if interactive:
            return Surface(page, on_message, level=STATUS_LEVEL + level_above_bar, shadow=False,
                           on_pointer=lambda: self.pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        return Surface(page, on_message, level=STATUS_LEVEL + level_above_bar, ignore_mouse=True, shadow=False, capturable=cap)

    def _build_surfaces(self) -> None:
        cap = self.capturable
        self.ghost = Ghost(cap)  # the dashed preview shown while dragging
        self.surface = Surface("overlay", self._on_bar, on_pointer=lambda: self.pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        for popup in self.popups.values():
            popup.build()

    def _all_surfaces(self) -> tuple:
        return (self.surface, *(p.surface for p in self.popups.values()), self.ghost)

    # ---- visibility
    def set_capturable(self, flag: bool) -> None:
        """Show the bar in screenshots and screen shares, or keep it out of them (live, from the setting)."""
        flag = bool(flag)
        if flag == self.capturable:
            return
        self.capturable = flag
        if flag:
            for surface in self._all_surfaces():
                surface.set_capturable(True)
            return
        # macOS does not take "visible to captures" back from a window that already has it (the window server keeps the old
        # state), so the panels are rebuilt: new ones start hidden from captures.
        was_visible = self.surface.visible
        self.close_popups()
        self.toasts.hide()
        old = self._all_surfaces()
        self.items, self.hover = [], None
        for popup in (self.menu, self.status, self.docs, self.notes):
            popup.reset_hit_state()
        self._build_surfaces()
        for surface in old:
            surface.panel.orderOut_(None)
            surface.panel.close()
        if was_visible:
            self.show()

    @property
    def visible(self) -> bool:
        return self.surface.visible

    def show(self) -> None:
        self._place()
        self.surface.show()

    def hide(self) -> None:
        self.close_popups()
        self.ctl.hide_now()  # (no sliding when the whole bar goes)
        self.toasts.hide()
        self.ghost.hide()
        self.surface.hide()

    def set_hidden(self, hidden: bool) -> None:
        """Collapse the bar to a slim tab on its screen edge, or bring it back."""
        self.hidden = hidden
        self.close_popups()
        self.toasts.hide()
        self.save()
        self.update(self.state)

    def update(self, state: dict) -> None:
        """Push the app's state to the bar (cheap; called every second and after every action)."""
        self.state = state
        self.surface.render({**state, "side": self.side, "hidden": self.hidden, "menuOpen": self.menu.wanted, "notesOpen": self.notes.wanted, "docsOpen": self.docs.wanted})
        if self.notes.wanted:
            self.notes.eval(f"window.__elapsed && window.__elapsed({int(state.get('elapsed', 0))})")  # Add time reads this clock
        if self.status.wanted:
            self.status.render_tabs()
        if self.docs.wanted:
            self.docs.render_list()
        if self.ctl.wanted:
            if not state.get("recording"):
                self.ctl.close()
            else:
                self.ctl.render({"kind": "ctl", "side": self.side, "paused": bool(state.get("paused"))})

    # ---- what the app calls on the popups
    def show_toast(self, lines: list, seconds: float = 2.0) -> None:
        self.toasts.show(lines, seconds)

    def toggle_notes(self) -> None:
        self.notes.toggle()

    def close_notes(self) -> None:
        self.notes.close()

    def reset_notes(self) -> None:
        self.notes.reset()

    def close_docs(self) -> None:
        self.docs.close()

    def open_status(self) -> None:
        self.status.open()

    def close_popups(self) -> None:
        self.ctl.close()
        self.docs.close()
        self.notes.close()
        self.tip.hide()
        self.menu.close()
        self.status.close()

    # ---- persistence: which edge, how far down, which display (a small file next to config.toml)
    def _file(self) -> Path:
        return config.settings_dir() / "overlay.json"

    def _load(self) -> None:
        try:
            d = json.loads(self._file().read_text())
            self.side = d["side"] if d.get("side") in ("left", "right") else "right"
            self.top = float(d["top"]) if d.get("top") is not None else None
            self.screen_id = d.get("display")
            self.hidden = bool(d.get("hidden", False))
            if d.get("notes_w") and d.get("notes_h"):
                self.notes.card = (float(d["notes_w"]), float(d["notes_h"]))
        except Exception:
            pass

    def save(self) -> None:
        try:
            card = self.notes.card or (None, None)
            self._file().write_text(json.dumps({"side": self.side, "top": self.top, "display": self.screen_id, "hidden": self.hidden,
                                                "notes_w": card[0], "notes_h": card[1]}))
        except OSError:
            pass

    # ---- placing the bar
    def _screen(self):
        return find_screen(self.screen_id) if self.screen_id is not None else NSScreen.mainScreen()

    def _place(self) -> None:
        """Put the panel on its edge: x from the side, y from the saved distance below the top (clamped to the screen)."""
        w, h = self._size
        v = self._screen().visibleFrame()
        if self.top is None:
            self.top = max((v.size.height - h) / 2, 0)
        inset = 0 if self.hidden else MARGIN
        x = v.origin.x + v.size.width - w - inset if self.side == "right" else v.origin.x + inset
        top = self.top + ((self._bar_h - h) / 2 if self.hidden else 0)  # a hidden tab keeps the bar's middle
        top = min(max(top, 0), max(v.size.height - h, 0))
        self.surface.panel.setFrame_display_(NSMakeRect(x, v.origin.y + v.size.height - top - h, w, h), True)
        self.surface.panel.invalidateShadow()

    def _resize(self, w: float, h: float) -> None:
        self._size = (max(24.0, min(w, 400.0)), max(40.0, min(h, 900.0)))
        if not self.hidden:
            self._bar_h = self._size[1]
        self._place()

    def _drag(self, msg) -> None:
        phase = msg.get("phase")
        if phase == "start":
            self.close_popups()
            o = self.surface.panel.frame().origin
            self._origin0 = (o.x, o.y)
        elif phase == "move" and self._origin0:
            x0, y0 = self._origin0
            self.surface.panel.setFrameOrigin_(NSMakePoint(x0 + float(msg["dx"]), y0 - float(msg["dy"])))  # screen y grows downward
            self._show_ghost()
        elif phase == "end" and self._origin0:
            self._origin0 = None
            self.ghost.hide()
            f = self.surface.panel.frame()
            screen = screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2))
            v = screen.visibleFrame()
            self.screen_id = screen_id(screen)
            self.side = "left" if f.origin.x + f.size.width / 2 < v.origin.x + v.size.width / 2 else "right"
            self.top = v.origin.y + v.size.height - (f.origin.y + f.size.height)
            if self.hidden:  # the stored top is the full bar's; a hidden tab is centred on it
                self.top -= (self._bar_h - f.size.height) / 2
            self._place()
            self.save()
            self.update(self.state)  # the side may have changed

    def _show_ghost(self) -> None:
        """Preview where the bar (or tab) will land if you let go now: the nearer edge, at the height being dragged to."""
        f = self.surface.panel.frame()
        mid = NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)
        v = screen_of(mid).visibleFrame()
        side = "left" if mid.x < v.origin.x + v.size.width / 2 else "right"
        w, h = f.size.width, f.size.height
        inset = 0 if self.hidden else MARGIN
        x = v.origin.x + inset if side == "left" else v.origin.x + v.size.width - w - inset
        y = min(max(f.origin.y, v.origin.y), v.origin.y + v.size.height - h)
        self.ghost.show(NSMakeRect(x, y, w, h), 14.0 if self.hidden else 20.0, side if self.hidden else None)

    # ---- requests from the bar page
    def _on_bar(self, msg) -> None:
        op = msg["op"]
        if op not in ("more", "tip_hide", "size", "ready", "hit"):  # anything you do on the bar closes the menu
            self.menu.close()
        if op in OPS:
            self.host.overlay_action(op)
        elif op == "docs":
            self.docs.toggle()
        elif op == "ready":
            self.update(self.state)
        elif op == "size":
            self._resize(float(msg["w"]), float(msg["h"]))
        elif op == "drag":
            self._drag(msg)
        elif op == "hit":
            self.items = [(str(i["id"]), float(i["x"]), float(i["y"]), float(i["w"]), float(i["h"])) for i in msg.get("items", [])]
            self.pointer_moved()
        elif op == "tip_hide":
            self.tip.hide()
        elif op == "more":
            self.menu.toggle({"top": float(msg.get("top", 0)), "bottom": float(msg.get("bottom", 0))})
        elif op == "status":
            self.status.toggle()
        elif op == "hide":
            self.set_hidden(True)
        elif op == "unhide":
            self.set_hidden(False)

    # ---- the pointer
    def pointer_moved(self) -> None:
        """Called on every mouse move anywhere: light up what the pointer is over, and show its tooltip."""
        if self._origin0 is not None or getattr(self, "surface", None) is None:  # dragging
            return
        p = NSEvent.mouseLocation()
        f = self.surface.panel.frame()
        listening = (self.menu, self.status, self.docs, self.ctl)  # the popups that read the pointer themselves
        if (self.hover is None and not any(popup.wanted for popup in listening) and self._poll is None
                and not (f.origin.x - 1 <= p.x <= f.origin.x + f.size.width + 1 and f.origin.y - 1 <= p.y <= f.origin.y + f.size.height + 1)):
            self._set_hand(False)
            return  # nowhere near us, nothing lit: nothing to do (this runs on every mouse move on the Mac)
        hit = None
        if self.surface.visible and f.origin.x <= p.x <= f.origin.x + f.size.width and f.origin.y <= p.y <= f.origin.y + f.size.height:
            for it_id, x, y, w, h in self.items:
                if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y:
                    hit = (it_id, y, y + h)
                    break
        if (hit[0] if hit else None) != self.hover:
            self.hover = hit[0] if hit else None
            self.surface.eval(f"window.__setHover && window.__setHover({js_literal(self.hover)})")  # the highlight: straight away
            if self.tip.timer is not None:
                self.tip.timer.invalidate()
                self.tip.timer = None
            if hit and hit[0] == "rec" and self.state.get("recording") and not self.hidden and not any(p.wanted for p in (self.menu, self.notes, self.docs, self.status)):
                self.ctl.hover_start(hit)  # resting on Stop opens Pause / Cancel (and no tooltip: it would sit in the same place)
            elif hit:
                # The tooltip is heavier (a panel is re-rendered, resized and moved). Sweeping across the toolbar would
                # queue up one of those per button and hold back the highlight, so it waits until the pointer settles.
                self.tip.timer = NSTimer.timerWithTimeInterval_repeats_block_(0.05, False, lambda t, h=hit: self.tip.due(h))
                NSRunLoop.mainRunLoop().addTimer_forMode_(self.tip.timer, NSRunLoopCommonModes)
            else:
                self.tip.hide()
        for popup in (self.menu, self.status, self.docs):
            if popup.wanted:
                popup.pointer()
        if self.ctl.wanted:
            self.ctl.pointer()
            self.ctl.check_leave(hit)
        # Mouse-moved events can be coalesced or delayed when the mouse moves fast, so while the pointer is over one of
        # our panels its position is also read 60 times a second: the highlight then always catches up.
        hand = hit is not None or any(popup.wanted and popup.hovering() for popup in listening)
        self._set_hand(hand)
        over = hit is not None or any(popup.wanted and popup.over() for popup in listening)
        if over and self._poll is None:
            self._poll = NSTimer.timerWithTimeInterval_repeats_block_(1 / 60, True, lambda t: self.pointer_moved())
            NSRunLoop.mainRunLoop().addTimer_forMode_(self._poll, NSRunLoopCommonModes)
        elif not over and self._poll is not None:
            self._poll.invalidate()
            self._poll = None

    def _set_hand(self, hand: bool) -> None:
        """The pointing-hand cursor over buttons. These panels are never the active window, so the page's own cursor
        style does not apply; the app sets it, and keeps setting it while the pointer stays over a button."""
        if hand:
            NSCursor.pointingHandCursor().set()
        elif self._hand:
            NSCursor.arrowCursor().set()
        self._hand = hand
