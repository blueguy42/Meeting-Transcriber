"""The floating control bar."""

import json
import logging
from pathlib import Path

from AppKit import NSCursor, NSEvent, NSScreen
from Foundation import NSMakePoint, NSMakeRect, NSRunLoop, NSRunLoopCommonModes, NSTimer

from .. import config
from .constants import GAP, MARGIN, MOVE_MASK, OPS, POPUP_PAD, STATUS_LEVEL, TOAST_ONE_LINE
from .docs import DocsMixin
from .menu import MenuMixin
from .notes import NotesMixin
from .status import StatusMixin
from .stop_menu import StopMenuMixin
from .surfaces import Ghost, Surface, allow_cursor_in_background, find_screen, js_literal, screen_id, screen_of
from .tips import TipsMixin
from .toast import ToastMixin


log = logging.getLogger("mt.overlay")


class Overlay(TipsMixin, ToastMixin, MenuMixin, StopMenuMixin, StatusMixin, NotesMixin, DocsMixin):
    """The bar itself (placement, dragging, visibility, which part of it the pointer is on). Each popup beside it has its own
    mixin: tooltips, toast, More menu, the Pause / Cancel bar, the processing panel, notes and documents."""

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
        self._state = {}  # what the app last told the bar
        self._anchor = {}  # popup -> the part of the bar it sits beside ({"top", "bottom"} in the bar page's coordinates)
        self._bar_items = []  # the toolbar buttons (id, x, y, w, h) in the bar page's coordinates
        self._bar_hover = None  # the button the pointer is on
        self._poll = None  # a 60 Hz look at the pointer while it is over one of our panels
        self._hand = False  # the pointing-hand cursor is showing (over a button in the bar or one of its popups)
        self._load()
        self._capturable = bool(getattr(host, "cfg", {}).get("overlay_in_captures", False))  # shown in screenshots and screen shares?
        self._want = {"tip": False, "menu": False, "status": False, "notes": False, "docs": False, "ctl": False, "toast0": False, "toast1": False}
        for init in (self._init_tips, self._init_menu, self._init_status, self._init_stop_menu, self._init_toast, self._init_docs, self._init_notes):
            init()  # each popup keeps its own state (see its module)
        self._build_surfaces()
        # Mouse-moved events reach a tooltip-style panel late or in clumps, but the app-wide monitors get them
        # promptly (hover had no lag while these were running for the open More menu), so they run all the time;
        # _pointer_moved leaves at once when the pointer is nowhere near us.
        self._move_monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(MOVE_MASK, lambda e: self._pointer_moved()),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(MOVE_MASK, lambda e: (self._pointer_moved(), e)[1]),
        ]

    def _popup(self, kind: str, level_above_bar: int = 1, interactive: bool = True, page: str = "popup.html") -> Surface:
        """One popup panel. Interactive ones hear the pointer (we work out what is under it ourselves); the others let clicks through."""
        cap = self._capturable
        if interactive:
            return Surface(page, lambda m: self._on_popup(kind, m), level=STATUS_LEVEL + level_above_bar, shadow=False,
                           on_pointer=lambda: self._pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        return Surface(page, lambda m: self._on_popup(kind, m), level=STATUS_LEVEL + level_above_bar, ignore_mouse=True, shadow=False, capturable=cap)

    def _build_surfaces(self) -> None:
        cap = self._capturable
        self.ghost = Ghost(cap)  # the dashed preview shown while dragging
        self.bar = Surface("overlay.html", self._on_bar, on_pointer=lambda: self._pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        self.tip = self._popup("tip", 2, interactive=False)  # tooltips float above every popup
        self.menu = self._popup("menu")
        self.status = self._popup("status")
        self.docs = self._popup("docs")
        self.ctl = self._popup("ctl")  # Pause / Cancel, attached to the Stop button
        self.toasts = [self._popup(kind, 3, interactive=False) for kind in ("toast0", "toast1")]  # two panels, used in turn; above tooltips
        self.notes = Surface("notes.html", self._on_notes, level=STATUS_LEVEL + 1, shadow=False, capturable=cap, can_key=True)
        self._popups = {"tip": self.tip, "menu": self.menu, "status": self.status, "notes": self.notes, "docs": self.docs,
                        "toast0": self.toasts[0], "toast1": self.toasts[1], "ctl": self.ctl}

    # ---- visibility
    def set_capturable(self, flag: bool) -> None:
        """Show the bar in screenshots and screen shares, or keep it out of them (live, from the setting)."""
        flag = bool(flag)
        if flag == self._capturable:
            return
        self._capturable = flag
        if flag:
            for surface in (self.bar, self.tip, self.menu, self.status, self.notes, self.docs, self.ctl, *self.toasts, self.ghost):
                surface.set_capturable(True)
            return
        # macOS does not take "visible to captures" back from a window that already has it (the window server keeps the old
        # state), so the panels are rebuilt: new ones start hidden from captures.
        was_visible = self.bar.visible
        self.close_popups()
        self.hide_toast()
        old = (self.bar, self.tip, self.menu, self.status, self.notes, self.docs, self.ctl, *self.toasts, self.ghost)
        self._bar_items, self._bar_hover, self._menu_rows, self._menu_rects, self._status_rows = [], None, [], [], []
        self._docs_rows = []
        self._notes_size = None
        self._build_surfaces()
        for surface in old:
            surface.panel.orderOut_(None)
            surface.panel.close()
        if was_visible:
            self.show()

    @property
    def visible(self) -> bool:
        return self.bar.visible

    def show(self) -> None:
        self._place()
        self.bar.show()

    def hide(self) -> None:
        self.close_popups()
        self._hide_ctl_now()  # (no sliding when the whole bar goes)
        self.hide_toast()
        self.ghost.hide()
        self.bar.hide()

    def set_hidden(self, hidden: bool) -> None:
        """Collapse the bar to a slim tab on its screen edge, or bring it back."""
        self.hidden = hidden
        self.close_popups()
        self.hide_toast()
        self._save()
        self.update(self._state)

    def update(self, state: dict) -> None:
        """Push the app's state to the bar (cheap; called every second and after every action)."""
        self._state = state
        self.bar.render({**state, "side": self.side, "hidden": self.hidden, "menuOpen": self._want["menu"], "notesOpen": self._want["notes"], "docsOpen": self._want["docs"]})
        if self._want["notes"]:
            self.notes.eval(f"window.__elapsed && window.__elapsed({int(state.get('elapsed', 0))})")  # Add time reads this clock
        if self._want["status"]:
            self._render_status()
        if self._want["docs"]:
            self._render_docs()
        if self._want["ctl"]:
            if not state.get("recording"):
                self.close_ctl()
            else:
                self.ctl.render({"kind": "ctl", "side": self.side, "paused": bool(state.get("paused"))})

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
                self._notes_card = (float(d["notes_w"]), float(d["notes_h"]))
        except Exception:
            pass

    def _save(self) -> None:
        try:
            card = self._notes_card or (None, None)
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
        self.bar.panel.setFrame_display_(NSMakeRect(x, v.origin.y + v.size.height - top - h, w, h), True)
        self.bar.panel.invalidateShadow()

    def _resize(self, w: float, h: float) -> None:
        self._size = (max(24.0, min(w, 400.0)), max(40.0, min(h, 900.0)))
        if not self.hidden:
            self._bar_h = self._size[1]
        self._place()

    def _drag(self, msg) -> None:
        phase = msg.get("phase")
        if phase == "start":
            self.close_popups()
            o = self.bar.panel.frame().origin
            self._origin0 = (o.x, o.y)
        elif phase == "move" and self._origin0:
            x0, y0 = self._origin0
            self.bar.panel.setFrameOrigin_(NSMakePoint(x0 + float(msg["dx"]), y0 - float(msg["dy"])))  # screen y grows downward
            self._show_ghost()
        elif phase == "end" and self._origin0:
            self._origin0 = None
            self.ghost.hide()
            f = self.bar.panel.frame()
            screen = screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2))
            v = screen.visibleFrame()
            self.screen_id = screen_id(screen)
            self.side = "left" if f.origin.x + f.size.width / 2 < v.origin.x + v.size.width / 2 else "right"
            self.top = v.origin.y + v.size.height - (f.origin.y + f.size.height)
            if self.hidden:  # the stored top is the full bar's; a hidden tab is centred on it
                self.top -= (self._bar_h - f.size.height) / 2
            self._place()
            self._save()
            self.update(self._state)  # the side may have changed

    def _show_ghost(self) -> None:
        """Preview where the bar (or tab) will land if you let go now: the nearer edge, at the height being dragged to."""
        f = self.bar.panel.frame()
        mid = NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)
        v = screen_of(mid).visibleFrame()
        side = "left" if mid.x < v.origin.x + v.size.width / 2 else "right"
        w, h = f.size.width, f.size.height
        inset = 0 if self.hidden else MARGIN
        x = v.origin.x + inset if side == "left" else v.origin.x + v.size.width - w - inset
        y = min(max(f.origin.y, v.origin.y), v.origin.y + v.size.height - h)
        self.ghost.show(NSMakeRect(x, y, w, h), 14.0 if self.hidden else 20.0, side if self.hidden else None)

    # ---- popups (tooltip, menu, status)
    def _bar_frame(self):
        return self.bar.panel.frame()

    def _place_popup(self, kind: str, w: float, h: float) -> None:
        f = self._bar_frame()
        v = screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        a = self._anchor.get(kind, {})
        pad = POPUP_PAD  # the panel is the cards plus this border all round
        if kind == "notes" and self._notes_size:
            w, h = self._notes_dims()
        x = f.origin.x - w - GAP + pad if self.side == "right" else f.origin.x + f.size.width + GAP - pad
        top, bottom = f.origin.y + f.size.height - a.get("top", 0), f.origin.y + f.size.height - a.get("bottom", 0)
        if kind == "notes":
            y = (top + bottom) / 2 - self._notes_size[1] / 2 if self._notes_size else (top + bottom) / 2 - h / 2  # bottom edge: as if it were the smallest size, it grows upwards
        elif kind == "ctl":
            y = (top + bottom) / 2 - h / 2  # level with the Stop button
            x = f.origin.x - w + pad if self.side == "right" else f.origin.x + f.size.width - pad  # touching the bar
        elif kind in ("toast0", "toast1"):
            y = (top + bottom) / 2 + TOAST_ONE_LINE / 2 - h + pad  # a one-line toast is level with the microphone button; a longer one keeps its top edge there
        elif kind == "tip" and self._tip_at:
            if self._tip_at[2] == "center":
                x = self._tip_at[0] - w / 2
                y = self._tip_at[1] - h + pad  # a few pixels under the icon
                if y < v.origin.y:  # no room below: above it
                    y = self._tip_at[3] - pad
            else:
                x = self._tip_at[0] - pad if self._tip_at[2] == "left" else self._tip_at[0] - w + pad
                y = self._tip_at[1] - h + pad  # under a card (the Pause / Cancel buttons, a tab)
                if len(self._tip_at) > 3 and y < v.origin.y:  # no room below: above it
                    y = self._tip_at[3] - pad
        elif kind == "tip":
            y = (top + bottom) / 2 - h / 2  # level with the middle of its button
        elif kind in ("menu", "docs"):
            y = bottom - pad  # the menu's (or the documents list's) bottom edge lines up with its button's
        else:
            y = f.origin.y + f.size.height - h + pad  # the status panel's top edge lines up with the bar's
        if kind == "tip" and self._tip_at:
            self._popups[kind].panel.setFrame_display_(NSMakeRect(x, y, w, h), True)
            if self._want[kind]:
                self._popups[kind].show()
            return
        x = min(max(x, v.origin.x - pad), v.origin.x + v.size.width - w + pad)
        y = min(max(y, v.origin.y - pad), v.origin.y + v.size.height - h + pad)
        self._popups[kind].panel.setFrame_display_(NSMakeRect(x, y, w, h), True)
        self._popups[kind].panel.invalidateShadow()
        if self._want[kind]:
            self._popups[kind].show()
            if kind == "notes" and self._notes_focus:  # opened just now: the keyboard goes to the notes
                self._notes_focus = False
                self._take_keyboard()
                t = NSTimer.timerWithTimeInterval_repeats_block_(0.15, False, lambda _t: self._take_keyboard())  # once more, in case the first try came too early
                NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)

    def _on_popup(self, kind: str, msg) -> None:
        op = msg["op"]
        if op == "size":
            self._place_popup(kind, float(msg["w"]), float(msg["h"]))
        elif op == "rects" and kind in ("menu", "status", "docs", "ctl"):
            rows = [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]), int(r["i"])) for r in msg.get("rows", [])]
            if kind == "menu":
                self._menu_rects = [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])) for r in msg.get("rects", [])]
                self._menu_rows = rows
                self._menu_passthrough()
            elif kind == "docs":
                self._docs_rows = rows
                self._docs_pointer()
            elif kind == "ctl":
                self._ctl_rows = rows
                self._ctl_pointer()
            else:
                self._status_cards = [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])) for r in msg.get("rects", [])]
                self._status_rows = rows
                self._status_tips = {int(r["i"]): str(r.get("tip", "")) for r in msg.get("rows", []) if r.get("tip")}  # the icon buttons' tooltips...
                self._status_tabs = {int(r["i"]): str(r.get("sub", "")) for r in msg.get("rows", []) if r.get("tab")}  # tab rows: their hint carries the time
                self._status_pointer(reapply=True)
        elif op == "docs_delete":
            self.host.overlay_docs_action("delete", str(msg.get("id")))
            self._render_docs()
        elif op == "docs_add":
            self.host.overlay_action("docs_add")
        elif op in ("ctl_pause", "ctl_cancel"):
            self.close_ctl()
            self.host.overlay_action("pause" if op == "ctl_pause" else "cancel_rec")
        elif op == "docs_close":
            self.close_docs()
        elif op == "menu_click":
            self.close_menu()
            self.host.overlay_menu_click(str(msg["id"]))
        elif op == "status_close":
            self.close_status()
            self.host.overlay_status_action("close", None)
        elif op in ("status_summary", "status_folder", "status_record", "status_pause", "status_resume", "status_cancel", "status_again", "status_dismiss"):
            self.host.overlay_status_action(op[len("status_"):], msg.get("id"))





    @staticmethod
    def _later(seconds: float, fn):
        t = NSTimer.timerWithTimeInterval_repeats_block_(seconds, False, lambda _t: fn())
        NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)
        return t



    def _pointer_moved(self) -> None:
        """Called on every mouse move anywhere: light up what the pointer is over, and show its tooltip."""
        if self._origin0 is not None or getattr(self, "bar", None) is None:  # dragging
            return
        p = NSEvent.mouseLocation()
        f = self.bar.panel.frame()
        if (self._bar_hover is None and not self._want["menu"] and not self._want["status"] and not self._want["docs"] and not self._want["ctl"] and self._poll is None
                and not (f.origin.x - 1 <= p.x <= f.origin.x + f.size.width + 1 and f.origin.y - 1 <= p.y <= f.origin.y + f.size.height + 1)):
            self._set_hand(False)
            return  # nowhere near us, nothing lit: nothing to do (this runs on every mouse move on the Mac)
        hit = None
        if self.bar.visible and f.origin.x <= p.x <= f.origin.x + f.size.width and f.origin.y <= p.y <= f.origin.y + f.size.height:
            for it_id, x, y, w, h in self._bar_items:
                if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y:
                    hit = (it_id, y, y + h)
                    break
        if (hit[0] if hit else None) != self._bar_hover:
            self._bar_hover = hit[0] if hit else None
            self.bar.eval(f"window.__setHover && window.__setHover({js_literal(self._bar_hover)})")  # the highlight: straight away
            if self._tip_timer is not None:
                self._tip_timer.invalidate()
                self._tip_timer = None
            if hit and hit[0] == "rec" and self._state.get("recording") and not self.hidden and not any(self._want[k] for k in ("menu", "notes", "docs", "status")):
                self._ctl_hover_start(hit)  # resting on Stop opens Pause / Cancel (and no tooltip: it would sit in the same place)
            elif hit:
                # The tooltip is heavier (a panel is re-rendered, resized and moved). Sweeping across the toolbar would
                # queue up one of those per button and hold back the highlight, so it waits until the pointer settles.
                self._tip_timer = NSTimer.timerWithTimeInterval_repeats_block_(0.05, False, lambda t, h=hit: self._tip_due(h))
                NSRunLoop.mainRunLoop().addTimer_forMode_(self._tip_timer, NSRunLoopCommonModes)
            else:
                self.hide_tip()
        if self._want["menu"]:
            self._menu_passthrough()
        if self._want["status"]:
            self._status_pointer()
        if self._want["docs"]:
            self._docs_pointer()
        if self._want["ctl"]:
            self._ctl_pointer()
            self._ctl_check_leave(hit)
        # Mouse-moved events can be coalesced or delayed when the mouse moves fast, so while the pointer is over one of
        # our panels its position is also read 60 times a second: the highlight then always catches up.
        hand = hit is not None or (self._want["menu"] and self._menu_hover >= 0) or (self._want["status"] and self._status_hover >= 0) \
            or (self._want["docs"] and self._docs_hover >= 0) \
            or (self._want["ctl"] and self._ctl_hover >= 0)
        self._set_hand(hand)
        over = hit is not None or (self._want["menu"] and self._over(self.menu)) or (self._want["status"] and self._over(self.status)) or (self._want["docs"] and self._over(self.docs)) or (self._want["ctl"] and self._over(self.ctl))
        if over and self._poll is None:
            self._poll = NSTimer.timerWithTimeInterval_repeats_block_(1 / 60, True, lambda t: self._pointer_moved())
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

    def _over(self, surface) -> bool:
        p = NSEvent.mouseLocation()
        f = surface.panel.frame()
        return f.origin.x <= p.x <= f.origin.x + f.size.width and f.origin.y <= p.y <= f.origin.y + f.size.height
















    # The notes panel is as small as its page says at least, up to half as wide again, and up to the top of the bar tall.
    # Its bottom edge stays where it is; the grip is in the top corner away from the bar.
    NOTES_MAX_WIDTH = 1.5


















    def close_popups(self) -> None:
        self.close_ctl()
        self.close_docs()
        self.close_notes()
        self.hide_tip()
        self.close_menu()
        self.close_status()

    # ---- requests from the bar page
    def _on_bar(self, msg) -> None:
        op = msg["op"]
        if op not in ("more", "tip_hide", "size", "ready", "hit"):  # anything you do on the bar closes the menu
            self.close_menu()
        if op in OPS:
            self.host.overlay_action(op)
        elif op == "docs":
            self.toggle_docs()
        elif op == "ready":
            self.update(self._state)
        elif op == "size":
            self._resize(float(msg["w"]), float(msg["h"]))
        elif op == "drag":
            self._drag(msg)
        elif op == "hit":
            self._bar_items = [(str(i["id"]), float(i["x"]), float(i["y"]), float(i["w"]), float(i["h"])) for i in msg.get("items", [])]
            self._pointer_moved()
        elif op == "tip_hide":
            self.hide_tip()
        elif op == "more":
            self.toggle_menu({"top": float(msg.get("top", 0)), "bottom": float(msg.get("bottom", 0))})
        elif op == "status":
            self.toggle_status()
        elif op == "hide":
            self.set_hidden(True)
        elif op == "unhide":
            self.set_hidden(False)
