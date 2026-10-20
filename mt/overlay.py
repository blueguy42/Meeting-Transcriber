"""The floating control bar: a small always-on-top panel hosting mt/ui/overlay.html in a WKWebView.

Same bridge idea as the Settings window (no web server, no open port). The page only sends a fixed set of
requests; the app pushes its state to the page as JSON. The panels never take focus from the app you are in
(Zoom, Meet...), so clicking them does not interrupt a call.

Three small extra panels (all hosting mt/ui/popup.html) sit beside the bar, on the side facing the screen
centre: a tooltip, the More menu (with the Summary style submenu) and the processing status.

The `host` (the menu bar App) supplies the content and does the work:
    host.overlay_action(op)            "toggle" | "shot" | "mute_mic" | "mute_system" | "perm"
    host.overlay_tip(id) -> {"text", "hotkey"} | None
    host.overlay_menu() -> [{"id", "label", "hint"?, "sub"?: [{"id", "label", "checked"}]} | {"divider": True}]
    host.overlay_menu_click(id)
    host.overlay_status() -> {"jobs": [...], "canRecord": bool}
    host.overlay_status_action(act, job_id)   "summary" | "folder" | "record" | "close"
"""

import json
import os
import traceback
from pathlib import Path

import objc
from AppKit import (
    NSColor,
    NSEvent,
    NSPanel,
    NSScreen,
    NSTrackingArea,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from Foundation import NSMakePoint, NSMakeRect, NSObject
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

from . import config

UI = Path(__file__).parent / "ui"
NONACTIVATING_PANEL = 1 << 7
STATUS_LEVEL = 25  # NSStatusWindowLevel: above normal windows and the Dock, like the menu bar
MARGIN = 24  # gap between the bar and the screen edge
GAP = 8  # gap between the bar and a popup
CLICK_MASK = (1 << 1) | (1 << 3) | (1 << 25)  # left, right, other mouse down
# NSTrackingMouseEnteredAndExited | MouseMoved | ActiveAlways | InVisibleRect
TRACK_ALWAYS = 0x01 | 0x02 | 0x80 | 0x200
OPS = {"toggle", "shot", "mute_mic", "mute_system", "perm"}


class _OverlayPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class _OverlayWeb(WKWebView):
    def acceptsFirstMouse_(self, event):
        return True  # the first click acts; it must not be spent on "activating" the panel

    # The panels are never the key window and the app is never "active", and WebKit does not track the mouse
    # (no :hover, no pointerenter) in that state. So an "always active" tracking area reports the mouse to us
    # and we pass its position to the page, which works out what is under it (window.__hover in the pages).
    def updateTrackingAreas(self):
        objc.super(_OverlayWeb, self).updateTrackingAreas()
        old = getattr(self, "_always_area", None)
        if old is not None:
            self.removeTrackingArea_(old)
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), TRACK_ALWAYS, self, None)
        self.addTrackingArea_(area)
        self._always_area = area

    @objc.python_method
    def _report_hover(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        y = p.y if self.isFlipped() else self.bounds().size.height - p.y
        self.evaluateJavaScript_completionHandler_(f"window.__hover && window.__hover({p.x}, {y})", None)

    def mouseMoved_(self, event):
        self._report_hover(event)

    def mouseEntered_(self, event):
        self._report_hover(event)

    def mouseExited_(self, event):
        self.evaluateJavaScript_completionHandler_("window.__hover && window.__hover(null)", None)


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


class _Surface:
    """One transparent panel with a web page in it."""

    def __init__(self, page: str, on_message, level: int = STATUS_LEVEL, ignore_mouse: bool = False):
        self.on_message = on_message
        rect = NSMakeRect(0, 0, 64, 64)
        self.panel = _OverlayPanel.alloc().initWithContentRect_styleMask_backing_defer_(rect, NONACTIVATING_PANEL, 2, False)
        p = self.panel
        p.setReleasedWhenClosed_(False)
        p.setFloatingPanel_(True)
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setHidesOnDeactivate_(False)
        p.setLevel_(level)
        p.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setHasShadow_(True)
        p.setIgnoresMouseEvents_(ignore_mouse)
        p.setAcceptsMouseMovedEvents_(True)
        # NSWindowSharingNone keeps the panels out of screen shares and captures (MT_OVERLAY_CAPTURABLE=1: for testing only)
        p.setSharingType_(1 if os.environ.get("MT_OVERLAY_CAPTURABLE") else 0)

        cfg = WKWebViewConfiguration.alloc().init()
        controller = WKUserContentController.alloc().init()
        self._bridge = _OverlayBridge.alloc().initWithOwner_(self)  # keep a reference
        controller.addScriptMessageHandler_name_(self._bridge, "bridge")
        cfg.setUserContentController_(controller)
        self.web = _OverlayWeb.alloc().initWithFrame_configuration_(rect, cfg)
        self.web.setValue_forKey_(False, "drawsBackground")  # transparent corners around the rounded content
        self.web.setAutoresizingMask_(2 | 16)
        self.web.loadHTMLString_baseURL_((UI / page).read_text(), None)
        p.setContentView_(self.web)
        self.web.updateTrackingAreas()

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
        self.bar = _Surface("overlay.html", self._on_bar)
        self.tip = _Surface("popup.html", lambda m: self._on_popup("tip", m), level=STATUS_LEVEL + 1, ignore_mouse=True)
        self.menu = _Surface("popup.html", lambda m: self._on_popup("menu", m), level=STATUS_LEVEL + 1)
        self.status = _Surface("popup.html", lambda m: self._on_popup("status", m), level=STATUS_LEVEL + 1)
        self._popups = {"tip": self.tip, "menu": self.menu, "status": self.status}
        self._want = {"tip": False, "menu": False, "status": False}  # shown once the page has measured itself

    # ---- visibility
    @property
    def visible(self) -> bool:
        return self.bar.visible

    def show(self) -> None:
        self._place()
        self.bar.show()

    def hide(self) -> None:
        self.close_popups()
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
        self.bar.render({**state, "side": self.side, "hidden": self.hidden})
        if self._want["status"]:
            self._render_status()

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
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self._file().write_text(json.dumps({"side": self.side, "top": self.top, "display": self.screen_id, "hidden": self.hidden}))
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
        elif phase == "end" and self._origin0:
            self._origin0 = None
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

    # ---- popups (tooltip, menu, status)
    def _bar_frame(self):
        return self.bar.panel.frame()

    def _place_popup(self, kind: str, w: float, h: float) -> None:
        f = self._bar_frame()
        v = _screen_of(NSMakePoint(f.origin.x + f.size.width / 2, f.origin.y + f.size.height / 2)).visibleFrame()
        a = self._anchor.get(kind, {})
        x = f.origin.x - w - GAP if self.side == "right" else f.origin.x + f.size.width + GAP
        top, bottom = f.origin.y + f.size.height - a.get("top", 0), f.origin.y + f.size.height - a.get("bottom", 0)
        if kind == "tip":
            y = (top + bottom) / 2 - h / 2
        elif kind == "menu":
            y = bottom  # the menu's bottom edge lines up with the More button's
        else:
            y = f.origin.y + f.size.height - h  # the status panel's top edge lines up with the bar's
        x = min(max(x, v.origin.x), v.origin.x + v.size.width - w)
        y = min(max(y, v.origin.y), v.origin.y + v.size.height - h)
        self._popups[kind].panel.setFrame_display_(NSMakeRect(x, y, w, h), True)
        self._popups[kind].panel.invalidateShadow()
        if self._want[kind]:
            self._popups[kind].show()

    def _on_popup(self, kind: str, msg) -> None:
        op = msg["op"]
        if op == "size":
            self._place_popup(kind, float(msg["w"]), float(msg["h"]))
        elif op == "menu_click":
            self.close_menu()
            self.host.overlay_menu_click(str(msg["id"]))
        elif op == "status_close":
            self.close_status()
            self.host.overlay_status_action("close", None)
        elif op in ("status_summary", "status_folder", "status_record"):
            self.host.overlay_status_action(op[len("status_"):], msg.get("id"))

    def show_tip(self, tip_id: str, top: float, bottom: float) -> None:
        if self._want["menu"] or self._origin0:
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
        self._anchor["menu"] = anchor
        self._want["menu"] = True
        self.menu.render({"kind": "menu", "side": self.side, "items": self.host.overlay_menu(), "fresh": True})
        # a click anywhere outside our own windows closes the menu
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(CLICK_MASK, lambda e: self.close_menu())

    def close_menu(self) -> None:
        self._want["menu"] = False
        self.menu.hide()
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    def open_status(self) -> None:
        self.hide_tip()
        self._anchor["status"] = {}
        self._want["status"] = True
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
        self.hide_tip()
        self.close_menu()
        self.close_status()

    # ---- requests from the bar page
    def _on_bar(self, msg) -> None:
        op = msg["op"]
        if op != "more" and op != "tip" and op != "tip_hide" and op != "size" and op != "ready":
            self.close_menu()
        if op in OPS:
            self.host.overlay_action(op)
        elif op == "ready":
            self.update(self._state)
        elif op == "size":
            self._resize(float(msg["w"]), float(msg["h"]))
        elif op == "drag":
            self._drag(msg)
        elif op == "tip":
            self.show_tip(str(msg["id"]), float(msg["top"]), float(msg["bottom"]))
        elif op == "tip_hide":
            self.hide_tip()
        elif op == "more":
            self.toggle_menu({"top": float(msg.get("top", 0)), "bottom": float(msg.get("bottom", 0))})
        elif op == "status":
            self.toggle_status()
        elif op == "unhide":
            self.set_hidden(False)
