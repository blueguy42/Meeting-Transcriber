import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt import config, notes, settings_api
from mt.jobs import Tracker
from mt.summarize import NOTES_INSTRUCTIONS, notes_block, notes_lines


class Files(unittest.TestCase):
    def test_save_read_and_remove(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            notes.save_notes(d, "[02:10] Budget is 120M\nask Budi")
            self.assertEqual(notes.read_notes(d), "[02:10] Budget is 120M\nask Budi\n")
            self.assertFalse(list(d.glob("*.tmp")))
            notes.save_notes(d, "  \n")  # emptied: no file left behind
            self.assertFalse(notes.notes_file(d).exists())
            self.assertEqual(notes.read_notes(d), "")

    def test_lines_and_stamps(self):
        self.assertEqual(notes.line_count("[02:10] a\n\n[05:42]\nb\n"), 2)  # a time alone is not a line of notes
        self.assertEqual(notes.stamp(754), "[12:34]")
        self.assertEqual(notes.stamp(3942), "[1:05:42]")
        self.assertEqual(notes.stamp(-3), "[00:00]")


class Summary(unittest.TestCase):
    def test_notes_reach_the_request_whatever_the_style(self):
        cfg = {"use_notes": True}
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.assertEqual(notes_block(d, cfg), "")  # no notes, nothing added
            notes.save_notes(d, "[05:42] Dina owns the shortlist {not a format field}")
            block = notes_block(d, cfg)
            self.assertIn("Dina owns the shortlist {not a format field}", block)
            self.assertIn("Do not label anything", block)
            self.assertEqual(notes_lines(d, cfg), 1)
            self.assertEqual(notes_block(d, {"use_notes": False}), "")  # turned off in Settings
            self.assertEqual(notes_lines(d, {"use_notes": False}), 0)
        self.assertIn("{notes}", NOTES_INSTRUCTIONS)


class Settings(unittest.TestCase):
    def test_notes_settings(self):
        self.assertEqual(settings_api._check("hotkey_notes", "<ctrl>+<alt>+t"), "<ctrl>+<alt>+t")
        self.assertEqual(settings_api._check("hotkey_notes_time", "<cmd>+t"), "<cmd>+t")
        with self.assertRaises(ValueError):
            settings_api._check("hotkey_notes", "t")
        with self.assertRaises(ValueError):
            settings_api._check("use_notes", "yes")
        self.assertTrue(settings_api._check("use_notes", False) is False)
        self.assertEqual(settings_api._check("hotkey_documents", "<ctrl>+<alt>+d"), "<ctrl>+<alt>+d")
        self.assertIs(settings_api._check("documents_ocr", True), True)
        with self.assertRaises(ValueError):
            settings_api._check("use_documents", "yes")
        self.assertTrue(config.defaults()["use_documents"] and config.defaults()["documents_ocr"])
        self.assertEqual(config.defaults()["hotkey_notes"], "<ctrl>+<alt>+t")
        self.assertTrue(config.defaults()["use_notes"])


class Flyout(unittest.TestCase):
    def test_summarizing_step_says_notes_are_used(self):
        t = Tracker(("Me", "Others"))
        t.add(Path("/x/2026-10-20_1402"), summarize=True, notes=4)
        steps = t.snapshot()[0]["steps"]
        self.assertEqual(next(s for s in steps if s["key"] == "summarize")["detail"], "with your notes (4 lines)")
        t.add(Path("/x/2026-10-20_1500"), summarize=True)
        self.assertNotIn("detail", next(s for s in t.snapshot()[1]["steps"] if s["key"] == "summarize"))


if __name__ == "__main__":
    unittest.main()
