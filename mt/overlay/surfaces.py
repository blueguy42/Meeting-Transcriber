"""The transparent web-view panels the floating bar is made of: a surface (panel + page + bridge), the drag ghost, and screen lookups."""

import json
import logging
import os

import objc
from AppKit import (
    NSBezierPath,
    NSColor,
    NSCursor,
    NSPanel,
    NSScreen,
    NSTrackingArea,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from Foundation import NSMakeRect, NSObject
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

from .constants import NONACTIVATING_PANEL, STATUS_LEVEL, TRACK_ALWAYS, UI

log = logging.getLogger("mt.overlay")


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


def allow_cursor_in_background() -> bool:
    """Let this app change the mouse cursor while another app is in front.

    macOS ignores NSCursor.set() from an app that is not active, and these panels never make the app active (so they never
    interrupt a call): the pointing hand worked only until you clicked another window. The window server has a per-app switch
    for this, "SetsCursorInBackground" (private, but long stable); without it everything else still works, just without the hand."""
    try:
        import ctypes

        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cg.CGSMainConnectionID.restype = ctypes.c_int
        cg.CGSSetConnectionProperty.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        cg.CGSSetConnectionProperty.restype = ctypes.c_int
        cid = cg.CGSMainConnectionID()
        key = cf.CFStringCreateWithCString(None, b"SetsCursorInBackground", 0x08000100)  # UTF-8
        yes = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        ok = cg.CGSSetConnectionProperty(cid, cid, key, yes) == 0
        if not ok:
            log.warning("Could not let the app set the cursor while in the background")
        return ok
    except Exception:
        log.exception("Could not let the app set the cursor while in the background")
        return False


def js_literal(value) -> str:
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


class Ghost:
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


class Surface:
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
            log.exception("Overlay message handler failed (op=%s)", msg.get("op"))

    def render(self, payload: dict) -> None:
        self.web.evaluateJavaScript_completionHandler_(f"window.render && window.render({js_literal(payload)})", None)

    @property
    def visible(self) -> bool:
        return bool(self.panel.isVisible())

    def eval(self, js: str) -> None:
        self.web.evaluateJavaScript_completionHandler_(js, None)

    def show(self) -> None:
        self.panel.orderFrontRegardless()

    def hide(self) -> None:
        self.panel.orderOut_(None)


def screen_of(point) -> NSScreen:
    for s in NSScreen.screens():
        f = s.frame()
        if f.origin.x <= point.x < f.origin.x + f.size.width and f.origin.y <= point.y < f.origin.y + f.size.height:
            return s
    return NSScreen.mainScreen()


def screen_id(screen) -> int:
    return int(screen.deviceDescription()["NSScreenNumber"])


def find_screen(wanted_id) -> NSScreen:
    for s in NSScreen.screens():
        if screen_id(s) == wanted_id:
            return s
    return NSScreen.mainScreen()  # that display is gone: fall back to the main one
