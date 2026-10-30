"""Small macOS helpers every part of the app shares: the main thread, notifications, dialogs, timers and the app's own icon."""

import logging
from pathlib import Path

from PyObjCTools import AppHelper

log = logging.getLogger("mt.desktop")


def on_main(fn, *args):
    """AppKit objects must only be touched from the main thread (hotkeys/workers run elsewhere)."""
    AppHelper.callAfter(fn, *args)


def notify(title, subtitle, message=""):
    def show():
        from Foundation import NSUserNotification, NSUserNotificationCenter

        n = NSUserNotification.alloc().init()
        n.setTitle_(title)
        n.setSubtitle_(subtitle)
        n.setInformativeText_(message)
        icon = app_icon()
        if icon is not None:
            try:
                n.setValue_forKey_(icon, "_identityImage")  # the logo instead of Python's rocket (a private key; ignored if it goes away)
            except Exception:
                pass
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        log.info("Notification: %s", f"{title}: {subtitle} {message}".strip())
        if center is None:  # not running from an app bundle: the log is all we have
            return
        center.deliverNotification_(n)

    on_main(show)


def alert(title: str, message: str, ok: str = "OK", cancel: str | None = None, destructive_second: bool = False):
    """A modal dialog. True if the first button (ok) was pressed. With destructive_second the answer is "first" or "second"
    instead, and the second button is drawn as the dangerous one (the first stays the default)."""
    from AppKit import NSAlert

    a = NSAlert.alloc().init()
    a.setMessageText_(title)
    a.setInformativeText_(message)
    icon = app_icon()
    if icon is not None:
        a.setIcon_(icon)
    a.addButtonWithTitle_(ok)
    if cancel:
        a.addButtonWithTitle_(cancel)
    if destructive_second:
        try:
            a.buttons()[1].setHasDestructiveAction_(True)
        except Exception:
            pass
        return "first" if a.runModal() == 1000 else "second"
    return a.runModal() == 1000  # NSAlertFirstButtonReturn


_timers = []  # NSTimers are kept alive here


def every(seconds: float, fn) -> None:
    """Call fn() every `seconds` on the main thread, also while a dialog (open panel, alert) is showing."""
    from AppKit import NSRunLoop, NSRunLoopCommonModes
    from Foundation import NSTimer

    t = NSTimer.timerWithTimeInterval_repeats_block_(seconds, True, lambda _t: fn())
    NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)
    _timers.append(t)


def bring_to_front():
    from AppKit import NSApp

    NSApp.activateIgnoringOtherApps_(True)  # an app without a Dock icon opens its dialogs behind other windows otherwise


def app_icon():
    """The app's logo (assets/AppIcon.icns). The app runs inside Python, so without this its dialogs show Python's rocket."""
    from pathlib import Path

    from AppKit import NSImage

    path = Path(__file__).resolve().parent.parent / "assets" / "AppIcon.icns"
    return NSImage.alloc().initWithContentsOfFile_(str(path)) if path.exists() else None


def hide_dock_icon():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    icon = app_icon()
    if icon is not None:
        app.setApplicationIconImage_(icon)  # alerts, open panels and the app switcher use this


_activity = None  # keeps the "do not nap" assertion alive for the life of the app


def stay_responsive():
    """An app that is never frontmost (this one has no Dock icon and its panels never take focus) is a prime
    candidate for App Nap: after a while macOS coalesces its timers and delays its events, which showed up as hover
    that got slower and slower. Ask not to be napped."""
    global _activity
    from Foundation import NSProcessInfo

    user_initiated, latency_critical = 0x00EFFFFF, 0xFF00000000  # NSActivityUserInitiatedAllowingIdleSystemSleep / NSActivityLatencyCritical
    _activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
        user_initiated | latency_critical, "The floating control bar has to react instantly"
    )
