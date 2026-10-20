import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt import progress
from mt.jobs import Tracker, display_name


class ProgressMessages(unittest.TestCase):
    def test_roundtrip(self):
        labels = ["Me", "Others"]
        self.assertEqual(progress.parse(progress.transcribing("Me", 0.62), labels), {"kind": "transcribing", "label": "Me", "fraction": 0.62})
        self.assertEqual(progress.parse(progress.transcribing("Others", bursts=14), labels)["fraction"], None)
        self.assertEqual(progress.parse(progress.SUMMARIZING, labels), {"kind": "summarizing"})
        self.assertEqual(progress.parse(progress.DOCX, labels), {"kind": "docx"})
        self.assertIsNone(progress.parse("Found 2 Others speaker(s)", labels))
        self.assertIsNone(progress.parse("Transcribing Nobody… 5%", labels))


class Hotkeys(unittest.TestCase):
    def test_fmt_hotkey(self):
        from mt.menubar import fmt_hotkey

        self.assertEqual(fmt_hotkey("<ctrl>+<alt>+r"), "⌃⌥R")
        self.assertEqual(fmt_hotkey("<cmd>+<shift>+<f5>"), "⌘⇧F5")
        self.assertIsNone(fmt_hotkey(""))


class Names(unittest.TestCase):
    def test_display_name(self):
        self.assertEqual(display_name("2026-10-20_1402"), "Meeting, Oct 20 · 14:02")
        self.assertEqual(display_name("2026-10-07_1030 - Q4 Budget Review"), "Q4 Budget Review")
        self.assertEqual(display_name("something else"), "something else")


class Flow(unittest.TestCase):
    def setUp(self):
        self.t = Tracker(("Me", "Others"))
        self.id = self.t.add(Path("/m/2026-10-20_1402"), summarize=True)

    def job(self):
        return self.t.snapshot()[0]

    def test_full_run(self):
        self.assertEqual(self.job()["state"], "queued")
        self.assertEqual(self.t.busy, 1)
        self.t.begin(self.id)
        self.assertEqual(self.job()["stage"], "transcribing")
        kids = self.job()["steps"][1]["children"]  # both tracks are listed from the start
        self.assertEqual([(c["label"], c["state"], c["sub"]) for c in kids], [("Your mic", "now", ""), ("System audio", "todo", "Waiting")])
        self.t.feed(self.id, progress.transcribing("Me", bursts=3))
        self.t.feed(self.id, progress.transcribing("Me", 1.0))
        self.t.feed(self.id, progress.transcribing("Others", bursts=9))
        self.t.feed(self.id, progress.transcribing("Others", 0.62))
        tr = self.job()["steps"][1]
        self.assertEqual([(c["label"], c["state"], c["sub"]) for c in tr["children"]], [("Your mic", "done", ""), ("System audio", "now", "62%")])
        p1 = self.job()["progress"]
        self.assertTrue(0.3 < p1 < 0.6, p1)
        self.t.feed(self.id, progress.SUMMARIZING)
        self.assertEqual(self.job()["stage"], "summarizing")
        self.assertEqual(self.job()["steps"][1]["state"], "done")
        self.assertGreater(self.job()["progress"], p1)
        self.t.feed(self.id, progress.DOCX)
        self.assertEqual(self.job()["stage"], "saving")
        self.t.finish(self.id, Path("/m/2026-10-20_1402 - Weekly sync"), Path("/m/x/summary.docx"))
        j = self.job()
        self.assertEqual((j["state"], j["progress"], j["name"]), ("done", 1.0, "Weekly sync"))
        self.assertEqual(self.t.busy, 0)
        self.t.acknowledge(self.id)
        self.assertEqual(self.t.snapshot(), [])

    def test_failure_and_unknown_messages(self):
        self.t.begin(self.id)
        self.t.feed(self.id, "Found 3 Others speaker(s)")  # ignored
        self.t.fail(self.id, "boom")
        j = self.job()
        self.assertEqual((j["state"], j["error"]), ("failed", "boom"))

    def test_without_summary(self):
        t = Tracker(("Me", "Others"))
        i = t.add(Path("/m/2026-10-20_1500"), summarize=False)
        t.begin(i)
        self.assertEqual([s["key"] for s in t.snapshot()[0]["steps"]], ["recorded", "transcribe", "save"])


if __name__ == "__main__":
    unittest.main()
