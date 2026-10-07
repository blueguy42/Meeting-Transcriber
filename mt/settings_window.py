"""The Settings window: a native macOS window hosting mt/ui/settings.html in a WKWebView.

The page talks to Python through a WKScriptMessageHandler ("bridge"): no web server, no network, no
open port. Every request is validated in settings_api.py.
"""

import json
import subprocess
import traceback
from pathlib import Path

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSOpenPanel,
    NSWindow,
)
from Foundation import NSObject
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

from . import settings_api

HTML = Path(__file__).parent / "ui" / "settings.html"
STYLE = 1 | 2 | 4 | 8  # titled, closable, miniaturizable, resizable


class _Bridge(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
    def initWithOwner_(self, owner):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        self._owner.handle(str(message.body()))


def _js(value) -> str:
    # U+2028/2029 are valid JSON but end a line in older JavaScript
    return json.dumps(value, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def install_edit_menu() -> None:
    """Menu bar apps have no main menu, so Cmd+C / Cmd+V / Cmd+A would not work in text fields."""
    main = NSMenu.alloc().init()
    app_item = NSMenuItem.alloc().init()
    main.addItem_(app_item)
    edit_item = NSMenuItem.alloc().init()
    main.addItem_(edit_item)
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (
        ("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a"), ("Close Window", "performClose:", "w"),
    ):
        edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    edit_item.setSubmenu_(edit)
    NSApp.setMainMenu_(main)


class SettingsWindow:
    def __init__(self, on_change=lambda: None):
        self.on_change = on_change
        self.window = None
        self.web = None

    def show(self) -> None:
        if self.window is None:
            self._build()
        else:
            self.web.evaluateJavaScript_completionHandler_("window.start()", None)  # refresh from disk
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def _build(self) -> None:
        install_edit_menu()
        cfg = WKWebViewConfiguration.alloc().init()
        controller = WKUserContentController.alloc().init()
        self._bridge = _Bridge.alloc().initWithOwner_(self)  # keep a reference
        controller.addScriptMessageHandler_name_(self._bridge, "bridge")
        cfg.setUserContentController_(controller)
        self.web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 780, 700), cfg)
        self.web.setAutoresizingMask_(2 | 16)
        self.web.loadHTMLString_baseURL_(HTML.read_text(), None)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 780, 700), STYLE, NSBackingStoreBuffered, False
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_("Meeting Transcriber Settings")
        self.window.setContentMinSize_((640, 520))
        self.window.setContentView_(self.web)
        self.window.center()

    # ---- requests from the page
    def handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            op, args, rid = msg["op"], msg.get("args") or {}, msg["id"]
        except Exception:
            return  # malformed: nothing to answer
        try:
            if op in settings_api.OPS:
                result = settings_api.OPS[op](args)
                if op in settings_api.CHANGES:
                    self.on_change()
            elif op == "choose_folder":
                result = self._choose_folder()
            elif op == "open_folder":
                result = self._open_folder(args.get("path", ""))
            else:
                raise ValueError(f"Unknown request: {op}")
            self._reply(rid, True, result)
        except (ValueError, KeyError) as e:
            self._reply(rid, False, str(e.args[0]) if e.args else "Invalid request")
        except Exception as e:
            traceback.print_exc()
            self._reply(rid, False, f"Unexpected error: {e}")

    def _reply(self, rid, ok: bool, payload) -> None:
        self.web.evaluateJavaScript_completionHandler_(f"window.__reply({_js(rid)}, {_js(ok)}, {_js(payload)})", None)

    def _choose_folder(self):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setCanCreateDirectories_(True)
        panel.setPrompt_("Choose")
        return str(panel.URL().path()) if panel.runModal() == 1 else None

    def _open_folder(self, path: str):
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise ValueError("The meetings folder must be an absolute path.")
        p.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(p)])
        return None
