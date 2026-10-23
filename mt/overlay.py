"""The floating control bar: a small always-on-top panel hosting mt/ui/overlay.html in a WKWebView.

Same bridge idea as the Settings window (no web server, no open port). The page only sends a fixed set of
requests; the app pushes its state to the page as JSON. The panels never take focus from the app you are in
(Zoom, Meet...), so clicking them does not interrupt a call.

Three small extra panels (all hosting mt/ui/popup.html) sit beside the bar, on the side facing the screen
centre: a tooltip, the More menu (with the Summary style submenu) and the processing status. A fourth, the notes
panel (mt/ui/notes.html), is the one panel that takes the keyboard, so you can type in it; it still does not
activate the app, so the meeting window you are in stays in front.

The `host` (the App in app.py) supplies the content and does the work:
    host.overlay_action(op)            "toggle" | "shot" | "notes" | "docs_add" | "mute_mic" | "mute_system" | "perm" | "quit"
    host.overlay_tip(id) -> {"text", "hotkey"} | None
    host.overlay_menu() -> [{"id", "label", "hint"?, "sub"?: [{"id", "label", "checked"}]} | {"divider": True}]
    host.overlay_menu_click(id)
    host.overlay_status() -> {"jobs": [...], "canRecord": bool}
    host.overlay_status_action(act, job_id)   "summary" | "folder" | "record" | "close"
    host.overlay_docs() -> {"docs": [...]}                  the documents list (see App.overlay_docs)
    host.overlay_docs_action("delete", id)
    host.overlay_notes_info() -> {"timeKey", "timeLabel"}   how the notes panel inserts the time
    host.overlay_notes_save(text) -> None | an error message
"""

import json
import os
import traceback
from pathlib import Path

import objc
from AppKit import (
    NSBezierPath,
    NSColor,
    NSCursor,
    NSEvent,
    NSPanel,
    NSScreen,
    NSTrackingArea,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from Foundation import NSMakePoint, NSMakeRect, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

from . import config

UI = Path(__file__).parent / "ui"
NONACTIVATING_PANEL = 1 << 7
STATUS_LEVEL = 25  # NSStatusWindowLevel: above normal windows and the Dock
MARGIN = 24  # gap between the bar and the screen edge
GAP = 8  # gap between the bar and a popup
POPUP_PAD = 14.0  # transparent border around a popup's cards (room for their CSS shadow), see popup.html
CLICK_MASK = (1 << 1) | (1 << 3) | (1 << 25)  # left, right, other mouse down
MOVE_MASK = 1 << 5  # mouse moved
# NSTrackingMouseEnteredAndExited | MouseMoved | ActiveAlways | InVisibleRect
TRACK_ALWAYS = 0x01 | 0x02 | 0x04 | 0x80 | 0x200  # (0x04: cursorUpdate)
OPS = {"toggle", "shot", "notes", "docs_add", "mute_mic", "mute_system", "perm", "quit"}


class _OverlayPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class _KeyPanel(_OverlayPanel):
    """The notes panel: it can take the keyboard (you type in it) without making the app active."""

    def canBecomeKeyWindow(self):
        return True


class _OverlayWeb(WKWebView):
    pointer_cb = None  # called whenever the mouse moves over, enters or leaves this panel
    hand_cb = None  # True while the pointer is over a button of this panel (see Overlay._hand)

    def acceptsFirstMouse_(self, event):
        return True  # the first click acts; it must not be spent on "activating" the panel

    # The pages cannot be trusted to hear about the mouse (their panels are never the key window), so the panel
    # itself listens: an "always active" tracking area reports the mouse to us, and the app works out what is under
    # it (see Overlay._pointer_moved). This only fires while the pointer is over one of our panels.
    def updateTrackingAreas(self):
        objc.super(_OverlayWeb, self).updateTrackingAreas()
        old = getattr(self, "_always_area", None)
        if old is not None:
            self.removeTrackingArea_(old)
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), TRACK_ALWAYS, self, None)
        self.addTrackingArea_(area)
        self._always_area = area

    def mouseMoved_(self, event):
        if self.pointer_cb:
            self.pointer_cb()

    # WebKit would put the arrow cursor back whenever the pointer enters the panel (it knows nothing about our hover
    # logic); the app decides instead: the pointing hand over a button, the arrow elsewhere.
    def cursorUpdate_(self, event):
        from AppKit import NSCursor

        (NSCursor.pointingHandCursor() if self.hand_cb and self.hand_cb() else NSCursor.arrowCursor()).set()

    def mouseEntered_(self, event):
        if self.pointer_cb:
            self.pointer_cb()

    def mouseExited_(self, event):
        if self.pointer_cb:
            self.pointer_cb()


class _OverlayBridge(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
    def initWithOwner_(self, owner):
        self = objc.super(_OverlayBridge, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        self._owner._receive(str(message.body()))


def _js(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


class _OverlayGhostView(NSView):
    """The dashed "preview" of where a dragged bar (or tab) will land."""

    def drawRect_(self, rect):
        b = self.bounds()
        r = float(getattr(self, "radius", 20.0))
        ext = getattr(self, "ext", None)  # a tab runs past the screen edge it is flush with, so that side stays open
        x, w = b.origin.x + 0.75, b.size.width - 1.5
        if ext == "right":
            w += r + 2
        elif ext == "left":
            x -= r + 2
            w += r + 2
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, b.origin.y + 0.75, w, b.size.height - 1.5), r, r)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.39, 0.66, 0.91, 0.14).setFill()
        path.fill()
        path.setLineWidth_(1.5)
        path.setLineDash_count_phase_([5.0, 4.0], 2, 0.0)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.39, 0.66, 0.91, 1.0).setStroke()
        path.stroke()


class _Ghost:
    def __init__(self, capturable: bool = False):
        rect = NSMakeRect(0, 0, 64, 64)
        p = self.panel = _OverlayPanel.alloc().initWithContentRect_styleMask_backing_defer_(rect, NONACTIVATING_PANEL, 2, False)
        p.setReleasedWhenClosed_(False)
        p.setFloatingPanel_(True)
        p.setHidesOnDeactivate_(False)
        p.setLevel_(STATUS_LEVEL - 1)  # under the bar
        p.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setHasShadow_(False)
        p.setIgnoresMouseEvents_(True)
        self.set_capturable(capturable)
        self.view = _OverlayGhostView.alloc().initWithFrame_(rect)
        p.setContentView_(self.view)

    def set_capturable(self, flag: bool) -> None:
        self.panel.setSharingType_(1 if (flag or os.environ.get("MT_OVERLAY_CAPTURABLE")) else 0)

    def show(self, frame, radius: float, ext) -> None:
        self.view.radius, self.view.ext = radius, ext
        self.panel.setFrame_display_(frame, True)
        self.view.setNeedsDisplay_(True)
        self.panel.orderFrontRegardless()

    def hide(self) -> None:
        self.panel.orderOut_(None)


class _Surface:
    """One transparent panel with a web page in it."""

    def __init__(self, page: str, on_message, level: int = STATUS_LEVEL, ignore_mouse: bool = False, shadow: bool = True, on_pointer=None, capturable: bool = False, can_key: bool = False, on_hand=None):
        self.on_message = on_message
        rect = NSMakeRect(0, 0, 64, 64)
        self.panel = (_KeyPanel if can_key else _OverlayPanel).alloc().initWithContentRect_styleMask_backing_defer_(rect, NONACTIVATING_PANEL, 2, False)
        p = self.panel
        p.setReleasedWhenClosed_(False)
        p.setFloatingPanel_(True)
        p.setBecomesKeyOnlyIfNeeded_(not can_key)  # a click in the notes panel must give it the keyboard
        p.setHidesOnDeactivate_(False)
        p.setLevel_(level)
        p.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setHasShadow_(shadow)
        p.setIgnoresMouseEvents_(ignore_mouse)
        p.setAcceptsMouseMovedEvents_(True)
        self.set_capturable(capturable)

        cfg = WKWebViewConfiguration.alloc().init()
        controller = WKUserContentController.alloc().init()
        self._bridge = _OverlayBridge.alloc().initWithOwner_(self)  # keep a reference
        controller.addScriptMessageHandler_name_(self._bridge, "bridge")
        cfg.setUserContentController_(controller)
        self.web = _OverlayWeb.alloc().initWithFrame_configuration_(rect, cfg)
        self.web.pointer_cb = on_pointer
        self.web.hand_cb = on_hand
        self.web.setValue_forKey_(False, "drawsBackground")  # transparent corners around the rounded content
        self.web.setAutoresizingMask_(2 | 16)
        self.web.loadHTMLString_baseURL_((UI / page).read_text(), None)
        p.setContentView_(self.web)
        self.web.updateTrackingAreas()
        self.web.updateTrackingAreas()

    def set_capturable(self, flag: bool) -> None:
        """NSWindowSharingNone keeps the panel out of screenshots and screen shares (the default);
        MT_OVERLAY_CAPTURABLE=1 forces it visible, for testing."""
        self.panel.setSharingType_(1 if (flag or os.environ.get("MT_OVERLAY_CAPTURABLE")) else 0)

    def _receive(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            msg["op"]
        except Exception:
            return
        try:
            self.on_message(msg)
        except Exception:
            traceback.print_exc()

    def render(self, payload: dict) -> None:
        self.web.evaluateJavaScript_completionHandler_(f"window.render && window.render({_js(payload)})", None)

    @property
    def visible(self) -> bool:
        return bool(self.panel.isVisible())

    def eval(self, js: str) -> None:
        self.web.evaluateJavaScript_completionHandler_(js, None)

    def show(self) -> None:
        self.panel.orderFrontRegardless()

    def hide(self) -> None:
        self.panel.orderOut_(None)


def _screen_of(point) -> NSScreen:
    for s in NSScreen.screens():
        f = s.frame()
        if f.origin.x <= point.x < f.origin.x + f.size.width and f.origin.y <= point.y < f.origin.y + f.size.height:
            return s
    return NSScreen.mainScreen()


def _screen_id(screen) -> int:
    return int(screen.deviceDescription()["NSScreenNumber"])


def _find_screen(screen_id) -> NSScreen:
    for s in NSScreen.screens():
        if _screen_id(s) == screen_id:
            return s
    return NSScreen.mainScreen()  # that display is gone: fall back to the main one


class Overlay:
    def __init__(self, host):
        self.host = host
        self.side = "right"  # which screen edge the bar is on ("left" or "right")
        self.hidden = False  # collapsed to the slim edge tab
        self.top = None  # distance from the top of the usable screen area to the top of the bar
        self.screen_id = None
        self._size = (64, 330)
        self._bar_h = 328.0  # the full bar's height: the hidden tab sits at its vertical middle
        self._origin0 = None
        self._state = {}
        self._monitor = None
        self._anchor = {}
        self._load()
        self._capturable = bool(getattr(host, "cfg", {}).get("overlay_in_captures", False))  # shown in screenshots and screen shares?
        self._build_surfaces()
        self._menu_rects = []  # the visible parts of the menu panel, in its own page coordinates
        self._menu_rows = []  # its rows (x, y, w, h, index), same coordinates
        self._bar_items = []  # the toolbar buttons (id, x, y, w, h) in the bar page's coordinates
        self._bar_hover = None
        self._tip_timer = None  # the tooltip waits a beat after the pointer settles, see _pointer_moved
        self._poll = None  # a 60 Hz look at the pointer while it is over one of our panels
        self._status_rows = []
        self._status_hover = -2
        self._menu_hover = -2  # the row the pointer is over (-1: none)
        # Mouse-moved events reach a tooltip-style panel late or in clumps, but the app-wide monitors get them
        # promptly (hover had no lag while these were running for the open More menu), so they run all the time;
        # _pointer_moved leaves at once when the pointer is nowhere near us.
        self._move_monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(MOVE_MASK, lambda e: self._pointer_moved()),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(MOVE_MASK, lambda e: (self._pointer_moved(), e)[1]),
        ]
        self._want = {"tip": False, "menu": False, "status": False, "notes": False, "docs": False}
        self._docs_rows = []
        self._docs_hover = -2
        self._hand = False  # the pointing-hand cursor is showing (over a button in the bar or one of its popups)
        self._notes_size = None  # the notes panel's smallest size, as measured by its page (it only reports when that changes)
        self._notes_card = None  # the size you dragged it to, (card width, card height): kept when the bar moves or changes side
        self._notes_focus = False  # shown once the page has measured itself

    def _build_surfaces(self) -> None:
        cap = self._capturable
        self.ghost = _Ghost(cap)  # the dashed preview shown while dragging
        self.bar = _Surface("overlay.html", self._on_bar, on_pointer=lambda: self._pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        self.tip = _Surface("popup.html", lambda m: self._on_popup("tip", m), level=STATUS_LEVEL + 2, ignore_mouse=True, shadow=False, capturable=cap)  # tooltips float above every popup
        self.menu = _Surface("popup.html", lambda m: self._on_popup("menu", m), level=STATUS_LEVEL + 1, shadow=False, on_pointer=lambda: self._pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        self.status = _Surface("popup.html", lambda m: self._on_popup("status", m), level=STATUS_LEVEL + 1, shadow=False, on_pointer=lambda: self._pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        self.docs = _Surface("popup.html", lambda m: self._on_popup("docs", m), level=STATUS_LEVEL + 1, shadow=False, on_pointer=lambda: self._pointer_moved(), on_hand=lambda: self._hand, capturable=cap)
        self.notes = _Surface("notes.html", self._on_notes, level=STATUS_LEVEL + 1, shadow=False, capturable=cap, can_key=True)
        self._popups = {"tip": self.tip, "menu": self.menu, "status": self.status, "notes": self.notes, "docs": self.docs}

    # ---- visibility
    def set_capturable(self, flag: bool) -> None:
        """Show the bar in screenshots and screen shares, or keep it out of them (live, from the setting)."""
        flag = bool(flag)
        if flag == self._capturable:
            return
        self._capturable = flag
        if flag:
            for surface in (self.bar, self.tip, self.menu, self.status, self.notes, self.docs, self.ghost):
                surface.set_capturable(True)
            return
        # macOS does not take "visible to captures" back from a window that already has it (the window server keeps the old
        # state), so the panels are rebuilt: new ones start hidden from captures.
        was_visible = self.bar.visible
        self.close_popups()
        old = (self.bar, self.tip, self.menu, self.status, self.notes, self.docs, self.ghost)
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
        self.ghost.hide()
        self.bar.hide()

    def set_hidden(self, hidden: bool) -> None:
        """Collapse the bar to a slim tab on its screen edge, or bring it back."""
        self.hidden = hidden
        self.close_popups()
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
        return _find_screen(self.screen_id) if self.screen_id is not None else NSScreen.mainScreen()

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
            screen = _screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2))
            v = screen.visibleFrame()
            self.screen_id = _screen_id(screen)
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
        v = _screen_of(mid).visibleFrame()
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
        v = _screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        a = self._anchor.get(kind, {})
        pad = POPUP_PAD  # the panel is the cards plus this border all round
        if kind == "notes" and self._notes_size:
            w, h = self._notes_dims()
        x = f.origin.x - w - GAP + pad if self.side == "right" else f.origin.x + f.size.width + GAP - pad
        top, bottom = f.origin.y + f.size.height - a.get("top", 0), f.origin.y + f.size.height - a.get("bottom", 0)
        if kind == "notes":
            y = (top + bottom) / 2 - self._notes_size[1] / 2 if self._notes_size else (top + bottom) / 2 - h / 2  # bottom edge: as if it were the smallest size, it grows upwards
        elif kind == "tip":
            y = (top + bottom) / 2 - h / 2  # level with the middle of its button
        elif kind in ("menu", "docs"):
            y = bottom - pad  # the menu's (or the documents list's) bottom edge lines up with its button's
        else:
            y = f.origin.y + f.size.height - h + pad  # the status panel's top edge lines up with the bar's
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
        elif op == "rects" and kind in ("menu", "status", "docs"):
            rows = [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]), int(r["i"])) for r in msg.get("rows", [])]
            if kind == "menu":
                self._menu_rects = [(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])) for r in msg.get("rects", [])]
                self._menu_rows = rows
                self._menu_passthrough()
            elif kind == "docs":
                self._docs_rows = rows
                self._docs_pointer()
            else:
                self._status_rows = rows
                self._status_pointer()
        elif op == "docs_delete":
            self.host.overlay_docs_action("delete", str(msg.get("id")))
            self._render_docs()
        elif op == "docs_add":
            self.host.overlay_action("docs_add")
        elif op == "docs_close":
            self.close_docs()
        elif op == "menu_click":
            self.close_menu()
            self.host.overlay_menu_click(str(msg["id"]))
        elif op == "status_close":
            self.close_status()
            self.host.overlay_status_action("close", None)
        elif op in ("status_summary", "status_folder", "status_record"):
            self.host.overlay_status_action(op[len("status_"):], msg.get("id"))

    def show_tip(self, tip_id: str, top: float, bottom: float) -> None:
        if self._origin0:
            return
        tip = self.host.overlay_tip(tip_id)
        if not tip:
            return
        self._anchor["tip"] = {"top": top, "bottom": bottom}
        self._want["tip"] = True
        self.tip.render({"kind": "tip", "side": self.side, **tip})

    def hide_tip(self) -> None:
        self._want["tip"] = False
        self.tip.hide()

    def toggle_menu(self, anchor: dict) -> None:
        if self._want["menu"]:
            self.close_menu()
            return
        self.hide_tip()
        self.close_status()  # one popup at a time: opening More closes the processing panel
        self.close_notes()
        self.close_docs()
        self._anchor["menu"] = anchor
        self._want["menu"] = True
        self.menu.render({"kind": "menu", "side": self.side, "items": self.host.overlay_menu(), "fresh": True})
        self.update(self._state)  # the More button stays lit while its menu is open
        # a click anywhere outside our own windows closes the menu
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(CLICK_MASK, lambda e: self.close_menu())
        # The panel is bigger than the menu (room for the submenu), so the empty part must not swallow clicks:
        # it ignores the mouse except while the pointer is over the menu itself.
        self.menu.panel.setIgnoresMouseEvents_(True)
        self._menu_hover = -2
        self._pointer_moved()

    def close_menu(self) -> None:
        was_open = self._want["menu"]
        self._want["menu"] = False
        self.menu.hide()
        if was_open:
            self.update(self._state)
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    def _pointer_moved(self) -> None:
        """Called on every mouse move anywhere: light up what the pointer is over, and show its tooltip."""
        if self._origin0 is not None or getattr(self, "bar", None) is None:  # dragging
            return
        p = NSEvent.mouseLocation()
        f = self.bar.panel.frame()
        if (self._bar_hover is None and not self._want["menu"] and not self._want["status"] and not self._want["docs"] and self._poll is None
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
            self.bar.eval(f"window.__setHover && window.__setHover({_js(self._bar_hover)})")  # the highlight: straight away
            if self._tip_timer is not None:
                self._tip_timer.invalidate()
                self._tip_timer = None
            if hit:
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
        # Mouse-moved events can be coalesced or delayed when the mouse moves fast, so while the pointer is over one of
        # our panels its position is also read 60 times a second: the highlight then always catches up.
        hand = hit is not None or (self._want["menu"] and self._menu_hover >= 0) or (self._want["status"] and self._status_hover >= 0) \
            or (self._want["docs"] and self._docs_hover >= 0)
        self._set_hand(hand)
        over = hit is not None or (self._want["menu"] and self._over(self.menu)) or (self._want["status"] and self._over(self.status)) or (self._want["docs"] and self._over(self.docs))
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

    def _tip_due(self, hit) -> None:
        self._tip_timer = None
        if self._bar_hover == hit[0]:
            self.show_tip(hit[0], hit[1], hit[2])

    def _status_pointer(self) -> None:
        if not self._want["status"]:
            return
        p = NSEvent.mouseLocation()
        f = self.status.panel.frame()
        row = next((i for x, y, w, h, i in self._status_rows
                    if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)
        if row != self._status_hover:
            self._status_hover = row
            self.status.eval(f"window.__setHoverRow && window.__setHoverRow({row})")

    def _menu_passthrough(self) -> None:
        """Let clicks through the menu panel everywhere except over the menu (and its submenu)."""
        if not self._want["menu"]:
            return
        p = NSEvent.mouseLocation()
        f = self.menu.panel.frame()
        inside = any(
            f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y
            for x, y, w, h in self._menu_rects
        )
        if bool(self.menu.panel.ignoresMouseEvents()) == inside:  # only touch the window when it actually changes
            self.menu.panel.setIgnoresMouseEvents_(not inside)
        # Which row is the pointer over? Worked out here from the mouse position, because the page does not
        # reliably hear about the mouse in a panel that is not the key window. The page is told only when it changes.
        row = next((i for x, y, w, h, i in self._menu_rows
                    if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)
        if row != self._menu_hover:
            self._menu_hover = row
            self.menu.eval(f"window.__setHoverRow && window.__setHoverRow({row})")

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

    # The notes panel is as small as its page says at least, up to half as wide again, and up to the top of the bar tall.
    # Its bottom edge stays where it is; the grip is in the top corner away from the bar.
    NOTES_MAX_WIDTH = 1.5

    def _notes_limits(self) -> tuple:
        """-> (min card w, min card h, max card w, max card h) for the bar where it is now."""
        pad = POPUP_PAD
        min_w, min_h = self._notes_size[0] - 2 * pad, self._notes_size[1] - 2 * pad
        f = self._bar_frame()
        a = self._anchor.get("notes", {})
        mid = f.origin.y + f.size.height - (a.get("top", 0) + a.get("bottom", 0)) / 2
        bottom = mid - self._notes_size[1] / 2  # the panel's bottom edge, in screen coordinates
        v = _screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
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
                self.notes.eval(f"window.__failed && window.__failed({_js(err)})")
        elif op == "notes_close":
            self.close_notes()

    # ---- documents list
    def toggle_docs(self) -> None:
        self.close_docs() if self._want["docs"] else self.open_docs()

    def open_docs(self) -> None:
        if self.hidden:
            self.set_hidden(False)
        item = next((i for i in self._bar_items if i[0] == "doclist"), None)
        if item is None:
            return
        self.hide_tip()
        self.close_menu()
        self.close_status()
        self.close_notes()
        self._anchor["docs"] = {"top": item[2], "bottom": item[2] + item[4]}
        self._want["docs"] = True
        self._docs_hover = -2
        self._render_docs()
        self.update(self._state)  # the list button stays lit while its list is open

    def close_docs(self) -> None:
        if not self._want["docs"]:
            return
        self._want["docs"] = False
        self.docs.hide()
        self.update(self._state)

    def _render_docs(self) -> None:
        """The list never grows taller than the bar (its top edge stops level with the bar's top, or the screen's): past that it scrolls."""
        a = self._anchor.get("docs", {})
        f = self._bar_frame()
        v = _screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        card_bottom = f.origin.y + f.size.height - a.get("bottom", 0)
        max_h = min(a.get("bottom", 420), v.origin.y + v.size.height - card_bottom - 2 * POPUP_PAD)
        self.docs.render({"kind": "docs", "side": self.side, "maxH": max_h, **self.host.overlay_docs()})

    def _docs_pointer(self) -> None:
        if not self._want["docs"]:
            return
        p = NSEvent.mouseLocation()
        f = self.docs.panel.frame()
        row = next((i for x, y, w, h, i in self._docs_rows
                    if f.origin.x + x <= p.x <= f.origin.x + x + w and f.origin.y + f.size.height - (y + h) <= p.y <= f.origin.y + f.size.height - y), -1)
        if row != self._docs_hover:
            self._docs_hover = row
            self.docs.eval(f"window.__setHoverRow && window.__setHoverRow({row})")

    def open_status(self) -> None:
        self.close_notes()
        self.close_docs()
        self.hide_tip()
        self._anchor["status"] = {}
        self._want["status"] = True
        self._status_hover = -2
        self._render_status()

    def toggle_status(self) -> None:
        self.close_status() if self._want["status"] else self.open_status()

    def close_status(self) -> None:
        self._want["status"] = False
        self.status.hide()

    def _render_status(self) -> None:
        data = self.host.overlay_status()
        if not data["jobs"]:
            self.close_status()
            return
        self.status.render({"kind": "status", "side": self.side, **data})

    def close_popups(self) -> None:
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
