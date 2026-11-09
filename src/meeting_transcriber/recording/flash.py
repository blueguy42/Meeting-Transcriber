"""A brief white flash over the screen that was captured, as visual feedback for a screenshot."""

from AppKit import (
    NSAnimationContext,
    NSBackingStoreBuffered,
    NSColor,
    NSEvent,
    NSScreen,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
)

_active: list = []  # keep windows alive until their fade-out finishes


def _screen_under_mouse():
    p = NSEvent.mouseLocation()
    for s in NSScreen.screens():
        f = s.frame()
        if f.origin.x <= p.x < f.origin.x + f.size.width and f.origin.y <= p.y < f.origin.y + f.size.height:
            return s
    return NSScreen.mainScreen()


def flash_screen(duration: float = 0.7) -> None:
    """Call on the main thread, after the screenshot was taken (so the flash is not in the picture)."""
    screen = _screen_under_mouse()
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(screen.frame(), 0, NSBackingStoreBuffered, False)
    win.setReleasedWhenClosed_(False)
    win.setBackgroundColor_(NSColor.whiteColor())
    win.setOpaque_(False)
    win.setIgnoresMouseEvents_(True)
    win.setLevel_(2000)  # above everything, like the system screenshot flash
    win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary)
    win.setAlphaValue_(0.7)
    win.orderFrontRegardless()
    _active.append(win)

    def done():
        win.orderOut_(None)
        _active.remove(win)

    def animate(ctx):
        ctx.setDuration_(duration)
        win.animator().setAlphaValue_(0.0)

    NSAnimationContext.runAnimationGroup_completionHandler_(animate, done)
