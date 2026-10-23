"""Screenshots: freeze the screen, let you choose an area, save only that area.

Pressing the screenshot button (or its shortcut) takes a picture of the screen under the mouse at once (so what you see is
exactly what is saved, even if a video keeps playing), shows it full screen with a dotted rectangle you drag, and the rest
dimmed. Two buttons sit inside the frame at its bottom right: ✕ cancels and ✓ takes the screenshot. Return and Esc do the same, and ⌘A selects
the whole screen. This module only does the choosing (AppKit, main thread); mt/recorder.py saves the picture.
"""

import AppKit
import objc
from AppKit import (
    NSApp,
    NSApplicationActivateIgnoringOtherApps,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSCursor,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakePoint,
    NSMakeRect,
    NSScreen,
    NSString,
    NSTrackingArea,
    NSView,
    NSWindow,
    NSWorkspace,
)

BUTTON = 28.0  # size of the ✕ and ✓ buttons (rounded squares in a dark box)
GAP = 7.0
INSET = 8.0
HANDLE = 9.0
EDGE = 14.0  # how close to an edge line the pointer grabs it (inside or outside the line)
CORNER_HIT = 20.0
HINT_TOP = 52.0  # the top hint sits below the camera notch / menu bar
MIN_SIDE = 6.0  # a drag shorter than this is a click, not an area
INSIDE_MIN = (100.0, 50.0)  # smaller areas have no room for the buttons inside: they sit just outside
LEVEL = 1500  # above the bar and its popups (25 and 26) and the Settings window
TRACK = 0x01 | 0x02 | 0x80 | 0x200  # entered/exited, mouse moved, active always, in visible rect

# resize handles: name -> (x factor, y factor) along the rectangle
HANDLES = {"nw": (0, 0), "n": (.5, 0), "ne": (1, 0), "w": (0, .5), "e": (1, .5), "sw": (0, 1), "s": (.5, 1), "se": (1, 1)}

FRAME_POSITIONS = {
    "n": AppKit.NSCursorFrameResizePositionTop, "s": AppKit.NSCursorFrameResizePositionBottom,
    "e": AppKit.NSCursorFrameResizePositionRight, "w": AppKit.NSCursorFrameResizePositionLeft,
    "nw": AppKit.NSCursorFrameResizePositionTopLeft, "ne": AppKit.NSCursorFrameResizePositionTopRight,
    "sw": AppKit.NSCursorFrameResizePositionBottomLeft, "se": AppKit.NSCursorFrameResizePositionBottomRight,
} if hasattr(AppKit, "NSCursorFrameResizePositionTop") else {}


def screen_under_mouse():
    p = NSEvent.mouseLocation()
    for s in NSScreen.screens():
        f = s.frame()
        if f.origin.x <= p.x < f.origin.x + f.size.width and f.origin.y <= p.y < f.origin.y + f.size.height:
            return s
    return NSScreen.mainScreen()


def freeze(screen):
    """The screen as a picture at its real pixel resolution (a Retina screen is 2x its size in points). None if it fails."""
    import Quartz

    return Quartz.CGDisplayCreateImage(int(screen.deviceDescription()["NSScreenNumber"]))


def button_rects(sel, bounds) -> tuple:
    """-> (cancel rect, add rect, "inside" | "below" | "above") for the finished selection (top-left origin, in points).
    Inside the frame at its bottom right when it is big enough; otherwise just outside it, below or above."""
    x, y, w, h = sel
    total = BUTTON * 2 + GAP
    if w >= INSIDE_MIN[0] and h >= INSIDE_MIN[1]:
        left, top, where = x + w - INSET - total, y + h - INSET - BUTTON, "inside"
    else:
        where = "below"
        top = y + h + INSET
        if top + BUTTON > bounds[1] - 4:
            where, top = "above", y - INSET - BUTTON
        left = x + w - total
    left = min(max(left, 4.0), bounds[0] - total - 4.0)
    top = min(max(top, 4.0), bounds[1] - BUTTON - 4.0)
    return (left, top, BUTTON, BUTTON), (left + BUTTON + GAP, top, BUTTON, BUTTON), where


def normalized(x0, y0, x1, y1, bounds):
    """A rectangle (x, y, w, h) between two corners, kept inside the screen."""
    x0, x1 = sorted((min(max(x0, 0.0), bounds[0]), min(max(x1, 0.0), bounds[0])))
    y0, y1 = sorted((min(max(y0, 0.0), bounds[1]), min(max(y1, 0.0), bounds[1])))
    return (x0, y0, x1 - x0, y1 - y0)


def to_pixels(sel, bounds, image_size) -> tuple:
    """A selection in points -> the crop box in the picture's pixels (x, y, w, h), at least one pixel."""
    sx, sy = image_size[0] / bounds[0], image_size[1] / bounds[1]
    x, y, w, h = sel
    px, py = int(round(x * sx)), int(round(y * sy))
    return px, py, max(1, min(int(round(w * sx)), image_size[0] - px)), max(1, min(int(round(h * sy)), image_size[1] - py))


def _inside(r, p) -> bool:
    return r[0] <= p[0] <= r[0] + r[2] and r[1] <= p[1] <= r[1] + r[3]


class _SelectWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True


class _SelectView(NSView):
    def isFlipped(self):
        return True  # top-left origin, like the picture's pixels

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def setup(self, image, pixels, on_done):
        self.image, self.pixels, self.on_done = image, pixels, on_done
        self.sel = None  # (x, y, w, h) in points, or None
        self.mode = None  # None | "new" | "move" | a handle name
        self.anchor = None
        self.start_sel = None
        self.ready = False  # the area is chosen (mouse released): buttons are shown
        self.mouse = None
        self.hover = None  # 'cancel' | 'add' while the pointer is on that button
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), TRACK, self, None))

    @objc.python_method
    def size(self):
        b = self.bounds()
        return (b.size.width, b.size.height)

    @objc.python_method
    def point(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        return (p.x, p.y)

    # ---- drawing
    def drawRect_(self, rect):
        b = self.bounds()
        self.image.drawInRect_(b)
        dim = NSBezierPath.bezierPathWithRect_(b)
        if self.sel is None:
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.28).setFill()
            dim.fill()
            self._hint("Drag to choose the area  ·  Esc cancels")
            return
        x, y, w, h = self.sel
        dim.appendBezierPath_(NSBezierPath.bezierPathWithRect_(NSMakeRect(x, y, w, h)))
        dim.setWindingRule_(1)  # even-odd: everything outside the area is dimmed, the area itself is not
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.4).setFill()
        dim.fill()
        border = NSBezierPath.bezierPathWithRect_(NSMakeRect(x, y, w, h))
        border.setLineWidth_(1.5)
        NSColor.whiteColor().setStroke()  # solid white line
        border.stroke()
        if self.ready or self.mode in HANDLES:
            for name in HANDLES:
                hx, hy = self._handle_point(name)
                dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(hx - HANDLE / 2, hy - HANDLE / 2, HANDLE, HANDLE))
                NSColor.whiteColor().setFill()
                dot.fill()
                NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.45).setStroke()
                dot.setLineWidth_(1.0)
                dot.stroke()
        cancel = add = None
        where = "inside"
        if self.ready:
            cancel, add, where = button_rects(self.sel, self.size())
            box = (cancel[0] - 5, cancel[1] - 5, BUTTON * 2 + GAP + 10, BUTTON + 10)  # a dark box behind both, so they show on any content
            NSColor.colorWithCalibratedWhite_alpha_(0.11, 0.96).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r_rect(box), 11, 11).fill()
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.32).setStroke()
            outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(box[0] + .5, box[1] + .5, box[2] - 1, box[3] - 1), 11, 11)
            outline.setLineWidth_(1.0)
            outline.stroke()
            self._button(cancel, False, self.hover == "cancel")
            self._button(add, True, self.hover == "add")
            if self.hover:
                r, text = (cancel, "Cancel  ·  Esc") if self.hover == "cancel" else (add, "Screenshot  ·  Return")
                ty = box[1] - 26
                self._pill(text, r[0] + BUTTON / 2, ty if ty >= 4 else box[1] + box[3] + 4, center=True)
        self._size_label(where)
        if self.ready:
            self._hint("Return takes the screenshot  ·  Esc cancels")

    @objc.python_method
    def _pill(self, text, x, y, size=11.0, center=False):
        attrs = {NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, 0.4), NSForegroundColorAttributeName: NSColor.whiteColor()}
        s = NSString.stringWithString_(text)
        sz = s.sizeWithAttributes_(attrs)
        w, h = sz.width + 16, sz.height + 6
        left = x - w / 2 if center else x
        NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.88).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(left, y, w, h), h / 2, h / 2).fill()
        s.drawAtPoint_withAttributes_(NSMakePoint(left + 8, y + 3), attrs)

    @objc.python_method
    def _hint(self, text):
        self._pill(text, self.size()[0] / 2, HINT_TOP, 12.0, center=True)

    @objc.python_method
    def _size_label(self, where):
        x, y, w, h = self.sel
        sx = self.pixels[0] / self.size()[0]
        text = f"{round(w * sx)} × {round(h * sx)} px"  # real screen pixels
        ty = y + h + 10 if where == "above" else y - 24
        if ty < 4 or (self.ready and ty < HINT_TOP + 30 and abs(x - self.size()[0] / 2) < 260):
            ty = y + 6  # no room above the area, or it would sit on the top hint: inside its top
        self._pill(text, x if where != "above" else x, min(max(ty, 4), self.size()[1] - 26))

    @objc.python_method
    def _button(self, r, add, hot=False):
        x, y, w, h = r
        if add:
            fill = (0.23, 0.525, 1.0) if hot else (0.039, 0.424, 1.0)  # lighter blue under the pointer
        else:
            fill = (0.48, 0.18, 0.17) if hot else (0.24, 0.24, 0.24)  # cancel turns red under the pointer
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*fill, 1.0).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r_rect(r), 7, 7).fill()
        NSColor.whiteColor().setStroke()
        glyph = NSBezierPath.bezierPath()
        glyph.setLineWidth_(2.4)
        glyph.setLineCapStyle_(1)
        glyph.setLineJoinStyle_(1)
        cx, cy = x + w / 2, y + h / 2
        if add:
            glyph.moveToPoint_(NSMakePoint(cx - 5.5, cy + 0.5))
            glyph.lineToPoint_(NSMakePoint(cx - 1.5, cy + 4.5))
            glyph.lineToPoint_(NSMakePoint(cx + 6, cy - 4))
        else:
            glyph.moveToPoint_(NSMakePoint(cx - 4.5, cy - 4.5))
            glyph.lineToPoint_(NSMakePoint(cx + 4.5, cy + 4.5))
            glyph.moveToPoint_(NSMakePoint(cx + 4.5, cy - 4.5))
            glyph.lineToPoint_(NSMakePoint(cx - 4.5, cy + 4.5))
        glyph.stroke()

    # ---- geometry
    @objc.python_method
    def _handle_point(self, name):
        x, y, w, h = self.sel
        fx, fy = HANDLES[name]
        return x + w * fx, y + h * fy

    @objc.python_method
    def _hit(self, p):
        """What is under the pointer: 'cancel' | 'add' | a handle name | 'inside' | None."""
        if self.sel is None:
            return None
        if self.ready:
            cancel, add, _where = button_rects(self.sel, self.size())
            if _inside(cancel, p):
                return "cancel"
            if _inside(add, p):
                return "add"
        x, y, w, h = self.sel
        for name in ("nw", "ne", "sw", "se"):  # corners first, with a generous reach
            hx, hy = self._handle_point(name)
            if abs(p[0] - hx) <= CORNER_HIT and abs(p[1] - hy) <= CORNER_HIT:
                return name
        band = min(EDGE, w / 3, h / 3)  # a small area keeps some inside to move it by
        if x - EDGE <= p[0] <= x + w + EDGE and y - EDGE <= p[1] <= y + h + EDGE:
            dist = {"n": abs(p[1] - y), "s": abs(p[1] - (y + h)), "w": abs(p[0] - x), "e": abs(p[0] - (x + w))}
            name = min(dist, key=dist.get)  # the nearest edge line, along its whole length
            if dist[name] <= band:
                return name
        return "inside" if _inside(self.sel, p) else None

    @objc.python_method
    def _cursor_for(self, hit):
        if hit in ("cancel", "add"):
            return NSCursor.pointingHandCursor()
        if hit in FRAME_POSITIONS:
            try:  # macOS 15+: proper edge and diagonal resize cursors
                return NSCursor.frameResizeCursorFromPosition_inDirections_(FRAME_POSITIONS[hit], AppKit.NSCursorFrameResizeDirectionsAll)
            except Exception:
                pass
        if hit in ("n", "s"):
            return NSCursor.resizeUpDownCursor()
        if hit in ("e", "w"):
            return NSCursor.resizeLeftRightCursor()
        if hit in ("nw", "se", "ne", "sw"):
            return NSCursor.crosshairCursor()
        if hit == "inside":
            return NSCursor.openHandCursor()
        return NSCursor.crosshairCursor()

    # ---- mouse
    def mouseMoved_(self, event):
        hit = self._hit(self.point(event))
        hover = hit if hit in ("cancel", "add") else None
        if hover != self.hover:
            self.hover = hover
            self.setNeedsDisplay_(True)
        self._cursor_for(hit).set()

    def mouseEntered_(self, event):
        self.mouseMoved_(event)

    def mouseDown_(self, event):
        p = self.point(event)
        hit = self._hit(p)
        if hit == "cancel":
            self.on_done(None)
            return
        if hit == "add":
            self.on_done(self.sel)
            return
        self.anchor, self.start_sel = p, self.sel
        if hit == "inside":
            self.mode = "move"
        elif hit in HANDLES:
            self.mode = hit
        else:
            self.mode, self.sel, self.ready = "new", None, False
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):
        p = self.point(event)
        size = self.size()
        ax, ay = self.anchor
        if self.mode == "new":
            if abs(p[0] - ax) >= 1 or abs(p[1] - ay) >= 1:
                self.sel = normalized(ax, ay, p[0], p[1], size)
        elif self.mode == "move" and self.start_sel:
            x, y, w, h = self.start_sel
            nx = min(max(x + p[0] - ax, 0.0), size[0] - w)
            ny = min(max(y + p[1] - ay, 0.0), size[1] - h)
            self.sel = (nx, ny, w, h)
        elif self.mode in HANDLES and self.start_sel:
            x, y, w, h = self.start_sel
            left, top, right, bottom = x, y, x + w, y + h
            if "w" in self.mode:
                left = p[0]
            if "e" in self.mode:
                right = p[0]
            if "n" in self.mode:
                top = p[1]
            if "s" in self.mode:
                bottom = p[1]
            self.sel = normalized(left, top, right, bottom, size)
        self.ready = False
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        if self.sel is not None and (self.sel[2] < MIN_SIDE or self.sel[3] < MIN_SIDE):
            self.sel = None  # a click, not an area
        self.ready = self.sel is not None
        self.mode = None
        self.hover = None
        self.setNeedsDisplay_(True)
        self.mouseMoved_(event)

    # ---- keys
    def selectAll_(self, sender):  # ⌘A reaches us through the Edit menu the app installs
        w, h = self.size()
        self.sel, self.ready, self.mode = (0.0, 0.0, w, h), True, None
        self.setNeedsDisplay_(True)

    def keyDown_(self, event):
        key = event.keyCode()
        if key == 53:  # Esc
            self.on_done(None)
        elif key in (36, 76):  # Return, Enter
            if self.sel is not None:
                self.on_done(self.sel)
        elif key == 0 and event.modifierFlags() & (1 << 20):  # ⌘A: the whole screen
            self.selectAll_(None)


def r_rect(r):
    return NSMakeRect(r[0], r[1], r[2], r[3])


class AreaSelector:
    """Shows the frozen screen and calls on_done(crop_box_in_pixels | None) once, when the area is added or cancelled."""

    def __init__(self, screen, cg_image, on_done):
        self.on_done = on_done
        self._finished = False
        w, h = cg_image_size(cg_image)
        frame = screen.frame()
        image = NSImage.alloc().initWithCGImage_size_(cg_image, (frame.size.width, frame.size.height))
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        self._previous = front if front is not None and front.processIdentifier() != _own_pid() else None
        self.window = _SelectWindow.alloc().initWithContentRect_styleMask_backing_defer_(frame, 0, NSBackingStoreBuffered, False)
        win = self.window
        win.setReleasedWhenClosed_(False)
        win.setLevel_(LEVEL)
        win.setOpaque_(True)
        win.setHasShadow_(False)
        win.setSharingType_(0)  # not in screen shares: only you see that you are choosing
        win.setCollectionBehavior_((1 << 0) | (1 << 8) | (1 << 4))  # all Spaces, over full-screen apps, stays put
        self.view = _SelectView.alloc().initWithFrame_(NSMakeRect(0, 0, frame.size.width, frame.size.height))
        self.view.setup(image, (w, h), self._done_points)
        win.setContentView_(self.view)
        self._pixels = (w, h)

    def show(self) -> None:
        NSApp.activateIgnoringOtherApps_(True)  # keys (Return, Esc) only reach a window of the active app
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.view)
        NSCursor.crosshairCursor().set()

    def _done_points(self, sel) -> None:
        if self._finished:
            return
        self._finished = True
        box = None if sel is None else to_pixels(sel, self.view.size(), self._pixels)
        self.window.orderOut_(None)
        NSCursor.arrowCursor().set()
        if self._previous is not None:
            self._previous.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)  # back to the meeting window
        self.on_done(box)

    def cancel(self) -> None:
        self._done_points(None)


def cg_image_size(cg) -> tuple[int, int]:
    import Quartz

    return int(Quartz.CGImageGetWidth(cg)), int(Quartz.CGImageGetHeight(cg))



def _own_pid() -> int:
    import os

    return os.getpid()
