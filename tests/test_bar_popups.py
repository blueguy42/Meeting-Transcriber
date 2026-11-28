"""The floating bar and the popups beside it, built offscreen with a fake host: opening one popup closes the others, the toasts
take turns, and the panels are rebuilt when the bar is taken out of (or put back into) screenshots."""

import tempfile
import unittest
from pathlib import Path

from AppKit import NSApplication
from Foundation import NSDate, NSRunLoop

from meeting_transcriber.bar import Bar
from meeting_transcriber.settings import config


class Host:
    def __init__(self):
        self.calls = []

    def overlay_in_captures(self): return False
    def overlay_action(self, op): self.calls.append(("action", op))
    def overlay_tip(self, tip_id): return {"text": f"tip {tip_id}", "hotkey": None}
    menu_items = [{"id": "settings", "label": "Settings…"}]
    def overlay_menu(self): return self.menu_items
    def overlay_menu_click(self, item_id): self.calls.append(("menu", item_id))
    status_jobs = [{"id": 1, "name": "Weekly sync", "state": "active", "stage": "x", "progress": 0.4, "when": "", "buttons": []}]
    def overlay_status(self): return {"jobs": self.status_jobs, "canRecord": True}
    def overlay_status_action(self, act, job_id, arg=None): self.calls.append(("status", act, job_id)); self.status_args = getattr(self, "status_args", []) + [arg]
    def overlay_docs(self): return {"docs": []}
    def overlay_docs_action(self, act, doc_id): self.calls.append(("docs", act, doc_id))
    def overlay_notes_info(self): return {"timeKey": "<cmd>+t", "timeLabel": "⌘T"}
    def overlay_notes_save(self, text): self.calls.append(("notes", text)); return None


def pump(seconds=0.4):
    NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


class Popups(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        NSApplication.sharedApplication()

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        original = config.settings_dir
        config.settings_dir = lambda: tmp  # the bar remembers where it sits: never touch the real settings folder
        self.addCleanup(setattr, config, "settings_dir", original)
        self.host = Host()
        self.bar = Bar(self.host)
        self.bar.show()
        pump(0.8)
        self.bar.update({"recording": True, "elapsed": 5, "paused": False, "busy": 1})
        self.bar.items = [(n, 10.0, 40.0 + 50 * k, 44.0, 44.0) for k, n in enumerate(["rec", "shot", "notes", "doclist", "mic", "sys", "more"])]
        self.addCleanup(self.bar.hide)

    def wanted(self):
        return {k for k, p in self.bar.popups.items() if p.wanted}

    def test_opening_a_popup_closes_the_others(self):
        bar = self.bar
        bar.menu.toggle({"top": 340, "bottom": 384})
        self.assertEqual(self.wanted(), {"menu"})
        bar.menu.close()
        bar.status.open()
        bar.docs.open()  # the lists and the notes close the processing panel
        self.assertEqual(self.wanted(), {"docs"})
        bar.notes.open()
        self.assertEqual(self.wanted(), {"notes"})
        bar.close_popups()
        self.assertEqual(self.wanted(), set())

    def test_the_menu_closes_the_panels_and_a_click_reaches_the_host(self):
        bar = self.bar
        bar.status.open()
        bar.docs.open()
        bar.menu.toggle({"top": 340, "bottom": 384})
        self.assertEqual(self.wanted(), {"menu"})
        bar.menu.on_message({"op": "menu_click", "id": "settings"})
        self.assertEqual(self.wanted(), set())
        self.assertIn(("menu", "settings"), self.host.calls)

    def test_status_buttons_and_the_pause_bar_reach_the_host(self):
        bar = self.bar
        bar.status.on_message({"op": "status_pause", "id": 1})
        bar.ctl.open(40, 84)
        self.assertTrue(bar.ctl.wanted)
        bar.ctl.on_message({"op": "ctl_cancel"})
        self.assertFalse(bar.ctl.wanted)
        self.assertEqual(self.host.calls, [("status", "pause", 1), ("action", "cancel_rec")])

    def test_toasts_take_turns_between_two_panels(self):
        toasts = self.bar.toasts
        toasts.show([("Mic", "AirPods")], 5)
        first = toasts.cur
        toasts.show([("System", "Speakers")], 5)
        self.assertNotEqual(first, toasts.cur)
        self.assertTrue(toasts.slots[toasts.cur].wanted)
        toasts.hide()
        self.assertFalse(any(s.wanted for s in toasts.slots))

    def test_the_bar_is_rebuilt_when_it_leaves_the_screenshots(self):
        bar = self.bar
        bar.set_capturable(True)
        before = bar.surface
        bar.set_capturable(False)
        self.assertIsNot(bar.surface, before)  # new panels: macOS does not take back "visible to captures"
        self.assertTrue(bar.visible)
        bar.menu.toggle({"top": 340, "bottom": 384})
        self.assertTrue(bar.menu.wanted)

    def test_a_hidden_bar_closes_its_popups_and_comes_back(self):
        bar = self.bar
        bar.docs.open()
        bar.set_hidden(True)
        self.assertEqual(self.wanted(), set())
        self.assertTrue(bar.hidden)
        bar.set_hidden(False)
        self.assertFalse(bar.hidden)

    def sizes_while(self, seconds, step=0.015):
        seen, end = [], seconds
        while end > 0:
            pump(step)
            end -= step
            f = self.bar.frame()
            seen.append((round(f.size.width), round(f.size.height)))
        return seen

    def widths_while(self, seconds, step=0.02):
        return [w for w, _h in self.sizes_while(seconds, step)]

    def test_hiding_closes_up_then_swipes_into_the_edge_and_showing_does_the_reverse(self):
        bar = self.bar
        bar._reduce_motion = lambda: False
        edge = bar.visible_frame().origin.x + bar.visible_frame().size.width
        full_w, full_h = round(bar.frame().size.width), round(bar.frame().size.height)
        bar.set_hidden(True)
        seen = self.sizes_while(0.9)
        tab_w, tab_h = seen[-1]
        self.assertEqual(tab_w, 42)
        self.assertLess(tab_h, full_h)
        self.assertTrue(any(w == full_w and tab_h < h < full_h for w, h in seen), seen)  # first it closes up, still as wide as the bar
        self.assertTrue(any(tab_w < w < full_w and h == tab_h for w, h in seen), seen)  # then it narrows to the tab, at the tab's height
        self.assertEqual([h for _w, h in seen], sorted((h for _w, h in seen), reverse=True))  # the height only ever goes down
        self.assertAlmostEqual(bar.frame().origin.x + bar.frame().size.width, edge, delta=1)  # flush with the edge
        self.assertIsNone(bar._morph)
        bar.set_hidden(False)
        seen = self.sizes_while(0.9)
        self.assertEqual(seen[-1], (full_w, full_h))
        self.assertTrue(any(tab_w < w < full_w and h == tab_h for w, h in seen), seen)  # first it widens at the tab's height
        self.assertTrue(any(w == full_w and tab_h < h < full_h for w, h in seen), seen)  # then it opens up
        self.assertEqual([h for _w, h in seen], sorted(h for _w, h in seen))
        self.assertAlmostEqual(bar.frame().origin.x + bar.frame().size.width, edge - 24, delta=1)  # back at its margin

    def test_with_reduce_motion_the_bar_jumps(self):
        bar = self.bar
        bar._reduce_motion = lambda: True
        bar.set_hidden(True)
        seen = self.widths_while(0.5)
        self.assertEqual(seen[-1], 42)
        self.assertLessEqual(set(seen), {64, 42})  # the bar or the tab: nothing in between
        self.assertIsNone(bar._morph)

    def test_clicking_again_while_it_slides_turns_it_around(self):
        bar = self.bar
        bar._reduce_motion = lambda: False
        full = round(bar.frame().size.width)
        bar.set_hidden(True)
        pump(0.1)
        bar.set_hidden(False)
        seen = self.widths_while(0.9)
        self.assertEqual(seen[-1], full)
        self.assertFalse(bar.hidden)

    def test_showing_without_animation_is_immediate_for_the_panels_that_need_the_bar(self):
        bar = self.bar
        bar._reduce_motion = lambda: False
        bar.set_hidden(True, animate=False)
        pump(0.3)
        bar.set_hidden(False, animate=False)
        pump(0.3)
        self.assertEqual(round(bar.frame().size.width), 64)
        self.assertIsNone(bar._morph)

    def evaluate(self, js):
        out = []
        self.bar.status.surface.web.evaluateJavaScript_completionHandler_(js, lambda r, e: out.append(r if e is None else str(e)))
        pump(0.25)
        return out[0] if out else None

    def finished_meeting(self, **extra):
        samples = [{"start": 80.0, "end": 85.0, "text": "Can I add something on the vendor side?", "at": "01:20"}, {"start": 99.0, "end": 104.0, "text": "And one more thing.", "at": "01:39"}]
        rows = [{"label": "Others 1", "name": "Budi", "by": "claude", "evidence": "Budi here", "at": "00:06", "lines": 2, "samples": samples, "sample": 0, "playing": False},
                {"label": "Others 2", "name": None, "by": None, "evidence": None, "lines": 1, "samples": samples, "sample": 0, "playing": False}]
        job = {"id": 1, "name": "Weekly sync", "state": "done", "stage": "done", "progress": 1.0, "when": "", "finished": 0, "mode": "full", "out": "/x", "folder": "/x",
               "error": "", "steps": [], "speakers": rows, "speakersStale": False, "speakersChecked": True, "canPlay": True, "canName": True, **extra}
        self.host.status_jobs = [job]
        self.bar.status.open()
        pump(0.3)

    def test_a_finished_meeting_lists_its_speakers_and_the_buttons_reach_the_app(self):
        self.finished_meeting()
        self.assertEqual(self.evaluate("document.querySelector('.sph .r').textContent"), "2 others · 1 named")
        self.assertFalse(self.evaluate("!!document.querySelector('.srow')"))  # collapsed to begin with
        self.evaluate("document.querySelector('.sph').click()")
        text = self.evaluate("document.querySelector('.spw').innerText")
        self.assertIn("Budi", text)
        self.assertIn("Who is this?", text)
        self.assertIn("Can I add something on the vendor side?", text)  # a line to recognise the unnamed one by
        self.assertIn("sample 1 of 2", text)
        for selector, act in ((".nm.ask", "rename"), (".sp.pl", "play"), (".nx", "nextsample")):
            self.evaluate(f"document.querySelector('{selector}').click()")
        self.assertEqual([c[1] for c in self.host.calls if c[0] == "status"], ["rename", "play", "nextsample"])
        self.assertEqual(self.host.status_args[0], "Others 2")

    def open_menu_with_two_submenus(self):
        self.host.menu_items = [
            {"id": "style", "label": "Summary style", "sub": [{"id": "style:default", "label": "default", "checked": True}, {"id": "style:short", "label": "short", "checked": False}]},
            {"id": "process", "label": "Process a meeting", "sub": [{"id": "transcribe", "label": "Transcribe a meeting…"}, {"id": "speakers", "label": "Name speakers in a meeting…"}, {"id": "summarize", "label": "Summarize a meeting…"}]},
            {"divider": True},
            {"id": "settings", "label": "Settings…"},
        ]
        self.bar.menu.toggle({"top": 340, "bottom": 384})
        pump(0.4)

    def menu_js(self, js):
        out = []
        self.bar.menu.surface.web.evaluateJavaScript_completionHandler_(js, lambda r, e: out.append(r if e is None else str(e)))
        pump(0.25)
        return out[0] if out else None

    def test_the_menu_can_have_two_submenus_each_level_with_its_own_row(self):
        self.open_menu_with_two_submenus()
        self.assertEqual(self.menu_js("document.querySelectorAll('.row[data-sub]').length"), 2)
        self.assertEqual(self.menu_js("document.querySelectorAll('.sub').length"), 2)
        tops = self.menu_js("Array.from(document.querySelectorAll('.sub')).map(c => Math.round(c.getBoundingClientRect().top)).join(',')").split(",")
        rows = self.menu_js("Array.from(document.querySelectorAll('.row[data-sub]')).map(r => Math.round(r.getBoundingClientRect().top)).join(',')").split(",")
        self.assertEqual(int(tops[1]) - int(tops[0]), int(rows[1]) - int(rows[0]))  # the second sits as far below the first as its row does
        self.assertGreater(int(rows[1]) - int(rows[0]), 20)

    def test_hovering_a_row_opens_its_submenu_and_only_that_one(self):
        self.open_menu_with_two_submenus()
        shown = "Array.from(document.querySelectorAll('.sub')).map(c => c.classList.contains('on') ? 1 : 0).join('')"
        self.assertEqual(self.menu_js(shown), "00")
        self.menu_js("window.__setHoverRow(Number(document.querySelector('.row[data-sub=\"2\"]').dataset.hi))")
        self.assertEqual(self.menu_js(shown), "01")
        self.menu_js("window.__setHoverRow(Number(document.querySelector('.row[data-sub=\"1\"]').dataset.hi))")
        self.assertEqual(self.menu_js(shown), "10")
        self.menu_js("window.__setHoverRow(Number(document.querySelector('.menu .row[data-id=\"settings\"]').dataset.hi))")
        self.assertEqual(self.menu_js(shown), "00")  # another row of the menu closes it

    def test_a_row_of_a_submenu_reaches_the_app_and_the_style_still_shows_its_tick(self):
        self.open_menu_with_two_submenus()
        self.assertEqual(self.menu_js("document.querySelector('.sub[data-subid=\"1\"] .k').textContent"), "✓")
        self.assertEqual(self.menu_js("document.querySelectorAll('.sub[data-subid=\"2\"] .k').length"), 0)  # no ticks in the other one
        self.menu_js("document.querySelector('.row[data-id=\"speakers\"]').click()")
        self.assertIn(("menu", "speakers"), self.host.calls)

    def test_a_named_speakers_sample_line_is_shown_only_while_it_matters(self):
        self.finished_meeting()
        self.evaluate("document.querySelector('.sph').click()")
        text = lambda: self.evaluate("Array.from(document.querySelectorAll('.srow')).map(r => (r.nextElementSibling && r.nextElementSibling.classList.contains('smp')) ? 1 : 0).join('')")
        self.assertEqual(text(), "01")  # Budi (named) has no sample line, the unnamed Others 2 has
        job = self.host.status_jobs[0]
        job["speakers"][0]["showSample"] = True  # (the app says so once Next sample was pressed on the named speaker)
        self.bar.status.render_tabs()
        pump(0.3)
        self.assertEqual(text(), "11")

    def test_a_stale_summary_offers_to_be_updated_and_a_deleted_recording_dims_the_play_button(self):
        self.finished_meeting(speakersStale=True, canPlay=False)
        self.evaluate("document.querySelector('.sph').click()")
        self.assertIn("Update summary", self.evaluate("document.querySelector('.spw').innerText"))
        self.assertTrue(self.evaluate("document.querySelector('.sp.pl').classList.contains('off')"))
        self.evaluate("document.querySelector('[data-act=resummarize]').click()")
        self.assertEqual(self.host.calls[-1][1], "resummarize")

    def test_a_meeting_without_other_speakers_shows_no_speakers_section(self):
        self.finished_meeting(speakers=[])
        self.assertFalse(self.evaluate("!!document.querySelector('.spw')"))


if __name__ == "__main__":
    unittest.main()
