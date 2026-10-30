"""The notes page in a real WebKit view (the panel uses WKWebView): adding a time must land exactly at the caret."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from AppKit import NSApplication
    from Foundation import NSDate, NSRunLoop
    from mt.overlay.surfaces import Surface

    HAVE_WEBKIT = True
except Exception:  # not on a Mac with PyObjC
    HAVE_WEBKIT = False

CARET = {
    "first": 'ed.innerHTML="HERE"; node=ed.firstChild; off=0',
    "line two start": 'ed.innerHTML="Some text<div><br></div><div>HERE</div>"; node=ed.querySelector("div:last-child").firstChild; off=0',
    "middle of a line": 'ed.innerHTML="Some text<div><br></div><div>HERE</div>"; node=ed.querySelector("div:last-child").firstChild; off=2',
    "end of a line": 'ed.innerHTML="Some text<div><br></div><div>HERE</div>"; node=ed.querySelector("div:last-child").firstChild; off=4',
    "empty line": 'ed.innerHTML="Some text<div><br></div><div>HERE</div>"; node=ed.querySelector("div"); off=0',
}
EXPECT = {
    "first": "[12:34] HERE",
    "line two start": "Some text\n\n[12:34] HERE",
    "middle of a line": "Some text\n\nHE[12:34] RE",
    "end of a line": "Some text\n\nHERE[12:34] ",
    "empty line": "Some text\n[12:34] \nHERE",
}


@unittest.skipUnless(HAVE_WEBKIT, "needs PyObjC and WebKit")
class AddTime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(1)
        cls.surface = Surface("notes.html", lambda m: None, can_key=True)
        cls.surface.panel.setFrame_display_(((100, 100), (360, 320)), True)
        cls.surface.show()
        cls.spin(1.0)
        cls.js('window.render({timeKey:"<cmd>+t",timeLabel:"x",elapsed:754,reset:true}); nowSeconds = () => 754; "ok"')  # (a frozen clock)

    @classmethod
    def tearDownClass(cls):
        cls.surface.panel.orderOut_(None)

    @staticmethod
    def spin(t):
        NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(t))

    @classmethod
    def js(cls, code):
        box = []
        cls.surface.web.evaluateJavaScript_completionHandler_(code, lambda r, e: box.append((r, e)))
        for _ in range(100):
            cls.spin(0.05)
            if box:
                break
        if not box or box[0][1] is not None:
            raise AssertionError(f"javascript failed: {box}")
        return box[0][0]

    def add_time_at(self, setup):
        self.js("{ let node, off; " + setup + "; ed.focus(); const r = document.createRange(); r.setStart(node, off); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); addTime(); }")
        return self.js("textOf()")

    def test_the_time_goes_exactly_at_the_caret_once(self):
        for name, setup in CARET.items():
            with self.subTest(name):
                out = self.add_time_at(setup)
                self.assertEqual(out, EXPECT[name])
                self.assertEqual(out.count("[12:34]"), 1)  # never doubled

    def test_the_time_is_a_chip_the_page_can_read_back(self):
        self.add_time_at(CARET["line two start"])
        self.assertEqual(self.js("ed.querySelectorAll('.ts').length"), 1)
        text = self.js("textOf()")
        self.js(f"fill({text!r})")  # saved text -> editor again
        self.assertEqual(self.js("ed.querySelectorAll('.ts').length"), 1)
        self.assertEqual(self.js("textOf()"), text)

    def test_backspace_removes_the_new_time_and_its_space_together(self):
        self.add_time_at(CARET["line two start"])
        self.js("ed.dispatchEvent(new KeyboardEvent('keydown', {key: 'Backspace', code: 'Backspace', bubbles: true, cancelable: true}))")
        self.assertEqual(self.js("textOf()"), "Some text\n\nHERE")

    def test_without_a_caret_the_time_goes_at_the_end(self):
        self.js('ed.innerHTML="note"; getSelection().removeAllRanges(); document.activeElement && document.activeElement.blur()')
        self.js("addTime()")
        self.assertEqual(self.js("textOf()"), "note[12:34] ")


if __name__ == "__main__":
    unittest.main()
