"""Boxes the screenshot chooser can snap to: the windows on screen and, when Accessibility is allowed, the buttons, panels
and other parts inside them (like Snipaste).

Windows (and the menu bar) come from Quartz's window list, which needs no permission beyond Screen Recording. The Dock's own
window covers the whole screen, so its box is the strip the Dock reserves instead (given by the caller). The finer boxes come from macOS
Accessibility, which only answers for apps that describe their parts and only when this app is allowed in System Settings →
Privacy & Security → Accessibility; without it you still get whole windows. Rectangles are in screen points with the origin at
the top left of the main display (what Quartz and Accessibility both use).
"""

DOCK_LAYER, MENU_BAR_LAYER = 20, 24
MIN_BOX = 12.0  # smaller boxes are not worth snapping to
MAX_DEPTH = 12  # how many levels up from the part under the pointer
AX_TIMEOUT = 0.15  # an app that does not answer in this long is skipped, so the pointer never lags


def windows(own_pid: int, dock=None) -> list[tuple[int, tuple]]:
    """On-screen normal windows of other apps, the menu bar and the Dock (at `dock`, a rectangle), front to back:
    [(pid, (x, y, w, h))]."""
    import Quartz

    out = []
    info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID) or []
    for w in info:
        layer = w.get("kCGWindowLayer", 1)
        if layer not in (0, DOCK_LAYER, MENU_BAR_LAYER) or w.get("kCGWindowAlpha", 1.0) <= 0.01:
            continue
        pid = int(w.get("kCGWindowOwnerPID", 0))
        b = w.get("kCGWindowBounds") or {}
        r = (float(b.get("X", 0)), float(b.get("Y", 0)), float(b.get("Width", 0)), float(b.get("Height", 0)))
        if layer == DOCK_LAYER:
            if dock is None:
                continue  # hidden Dock: nothing to point at
            r = tuple(dock)
        if pid != own_pid and r[2] >= MIN_BOX and r[3] >= MIN_BOX:
            out.append((pid, r))
    return out


def contains(r, p) -> bool:
    return r[0] <= p[0] <= r[0] + r[2] and r[1] <= p[1] <= r[1] + r[3]


def window_at(wins, p):
    """The front-most window under the point p: (pid, rect) or None."""
    return next((w for w in wins if contains(w[1], p)), None)


def boxes_from(window_rect, part_rects, p) -> list[tuple]:
    """Candidate boxes at p, smallest first: the parts that hold p (kept inside the window, no duplicates), then the window."""
    out = []
    if not contains(window_rect, p):
        return out
    for r in list(part_rects) + [window_rect]:
        if r[2] < MIN_BOX or r[3] < MIN_BOX or not contains(r, p):
            continue
        x0, y0 = max(r[0], window_rect[0]), max(r[1], window_rect[1])
        x1, y1 = min(r[0] + r[2], window_rect[0] + window_rect[2]), min(r[1] + r[3], window_rect[1] + window_rect[3])
        c = (x0, y0, x1 - x0, y1 - y0)
        if c[2] >= MIN_BOX and c[3] >= MIN_BOX and (not out or c != out[-1]) and (not out or c[2] * c[3] > out[-1][2] * out[-1][3]):
            out.append(c)
    return out


def accessible() -> bool:
    try:
        import ApplicationServices as A

        return bool(A.AXIsProcessTrusted())
    except Exception:
        return False


def part_rects(pid: int, p) -> list[tuple]:
    """Frames of the part of app `pid` under p and of its parents, smallest first. [] without the permission."""
    try:
        import ApplicationServices as A

        if not A.AXIsProcessTrusted():
            return []
        app = A.AXUIElementCreateApplication(pid)
        A.AXUIElementSetMessagingTimeout(app, AX_TIMEOUT)
        err, el = A.AXUIElementCopyElementAtPosition(app, p[0], p[1], None)
        if err != 0 or el is None:
            return []
        rects = []
        for _ in range(MAX_DEPTH):
            r = _frame(A, el)
            if r:
                rects.append(r)
            err, parent = A.AXUIElementCopyAttributeValue(el, A.kAXParentAttribute, None)
            if err != 0 or parent is None:
                break
            el = parent
        return rects
    except Exception:
        return []


def _frame(A, el):
    err, pos = A.AXUIElementCopyAttributeValue(el, A.kAXPositionAttribute, None)
    err2, size = A.AXUIElementCopyAttributeValue(el, A.kAXSizeAttribute, None)
    if err or err2 or pos is None or size is None:
        return None
    ok1, pt = A.AXValueGetValue(pos, A.kAXValueCGPointType, None)
    ok2, sz = A.AXValueGetValue(size, A.kAXValueCGSizeType, None)
    if not (ok1 and ok2):
        return None
    return (float(pt.x), float(pt.y), float(sz.width), float(sz.height))


def boxes_at(wins, p) -> list[tuple]:
    """All the boxes to snap to at p (screen points), smallest first."""
    w = window_at(wins, p)
    if w is None:
        return []
    return boxes_from(w[1], part_rects(w[0], p), p)
