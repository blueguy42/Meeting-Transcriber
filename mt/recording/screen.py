"""Looking at the screen: the pointer, grabbing a display at its real resolution, and whether Screen Recording is allowed."""

import sys

from .. import permissions


def mouse_position() -> tuple[float, float]:
    """Cursor position in points with a top-left origin: the same grid as Quartz display bounds."""
    try:
        from AppKit import NSEvent, NSScreen

        p = NSEvent.mouseLocation()
        return p.x, NSScreen.screens()[0].frame().size.height - p.y
    except Exception:
        return 0, 0


def grab_display(x: float, y: float):
    """The display under (x, y) at its real pixel resolution (a Retina screen is 2x its point size)."""
    import Quartz
    from PIL import Image

    _, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
    did = next((d for d in ids if Quartz.CGRectContainsPoint(Quartz.CGDisplayBounds(d), (x, y))), Quartz.CGMainDisplayID())
    cg = Quartz.CGDisplayCreateImage(did)
    if cg is None:
        raise RuntimeError("could not capture the screen")
    return cg_to_pil(cg)


def cg_to_pil(cg):
    """A Quartz picture (CGImage) as a Pillow image."""
    import Quartz
    from PIL import Image

    w, h, stride = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg), Quartz.CGImageGetBytesPerRow(cg)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(cg))
    return Image.frombytes("RGB", (w, h), bytes(data), "raw", "BGRX", stride, 1)


def screen_access() -> bool:
    """macOS silently hands screenshot tools just the wallpaper when Screen Recording is not allowed."""
    if sys.platform != "darwin" or permissions.screen_granted():
        return True
    permissions.request_screen()  # shows the system prompt the first time
    return False
