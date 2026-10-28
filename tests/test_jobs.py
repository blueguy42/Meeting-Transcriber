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
        self.assertEqual(progress.parse(progress.diarizing(40), labels), {"kind": "diarizing", "fraction": 0.4, "note": ""})
        self.assertEqual(progress.parse(progress.MATCHING_VOICES, labels)["note"], "Matching voices")
        self.assertEqual(progress.parse(progress.downloading_model(2), labels)["note"], "Downloading model 2 of 2")
        self.assertEqual(progress.parse(progress.speakers_found("Others", 2), labels), {"kind": "diarized", "failed": False})
        self.assertEqual(progress.parse(progress.speakers_found("Others", 1), labels), {"kind": "diarized", "failed": False})
        self.assertEqual(progress.parse(progress.SPEAKERS_FAILED + "no models", labels), {"kind": "diarized", "failed": True})
        self.assertIsNone(progress.parse("Found 2 Nobody speaker(s)", labels))
        self.assertEqual(progress.parse(progress.reading_documents(3, 5), labels), {"kind": "readdocs", "done": 3, "total": 5})
        self.assertIsNone(progress.parse("Transcribing Nobody… 5%", labels))


class Hotkeys(unittest.TestCase):
    def test_fmt_hotkey(self):
        from mt.app import fmt_hotkey

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

    def test_telling_speakers_apart_is_a_part_of_transcribing(self):
        t = Tracker(("Me", "Others"))
        i = t.add(Path("/m/2026-10-20_1500"), summarize=True, diarize=True)
        t.begin(i)
        kids = lambda: t.snapshot()[0]["steps"][1]["children"]
        self.assertEqual([c["label"] for c in kids()], ["Your mic", "System audio", "Speakers"])
        t.feed(i, progress.transcribing("Me", 1.0))
        t.feed(i, progress.transcribing("Others", 1.0))
        t.feed(i, progress.diarizing(40))
        self.assertEqual([(c["state"], c["sub"]) for c in kids()], [("done", ""), ("done", ""), ("now", "40%")])
        p = t.snapshot()[0]["progress"]
        t.feed(i, progress.speakers_found("Others", 3))
        self.assertEqual(kids()[2]["state"], "done")
        self.assertGreater(t.snapshot()[0]["progress"], p)
        # off: no extra line
        j = t.add(Path("/m/2026-10-20_1600"), summarize=True)
        t.begin(j)  # (queued behind the first one, but begin() works on any job)
        self.assertEqual(len(t.snapshot()[1]["steps"][1]["children"]), 2)

    def test_downloading_the_speech_model_comes_first(self):
        t = Tracker(("Me", "Others"))
        i = t.add(Path("/m/2026-10-20_1800"), summarize=True)
        t.begin(i)
        kids = lambda: t.snapshot()[0]["steps"][1]["children"]
        self.assertEqual(progress.parse(progress.downloading_whisper(412), ["Me", "Others"]), {"kind": "whisper", "mb": 412})
        t.feed(i, progress.downloading_whisper(0))
        t.feed(i, progress.downloading_whisper(412))
        self.assertEqual([(c["label"], c["state"], c["sub"]) for c in kids()],
                         [("Downloading model", "now", "412 MB"), ("Your mic", "todo", "Waiting"), ("System audio", "todo", "Waiting")])
        self.assertEqual(t.snapshot()[0]["progress"], 0.0)  # the download does not move the ring
        t.feed(i, progress.transcribing("Me", 0.5))
        self.assertEqual([(c["state"], c["sub"]) for c in kids()], [("done", ""), ("now", "50%"), ("todo", "Waiting")])

    def test_reading_documents_and_the_summary_lines(self):
        t = Tracker(("Me", "Others"))
        i = t.add(Path("/m/2026-10-20_1700"), summarize=True, notes=4, documents=3, reading=True)
        steps = t.snapshot()[0]["steps"]
        self.assertEqual([s["key"] for s in steps], ["recorded", "readdocs", "transcribe", "summarize", "save"])
        self.assertEqual(next(s for s in steps if s["key"] == "summarize")["detail"], "with your notes (4 lines)\nand 3 documents")
        t.begin(i)
        self.assertEqual(t.snapshot()[0]["stage"], "reading")
        t.feed(i, progress.reading_documents(2, 5))
        self.assertEqual(t.snapshot()[0]["steps"][1]["sub"], "2 of 5")
        t.feed(i, progress.transcribing("Me", bursts=2))
        self.assertEqual((t.snapshot()[0]["steps"][1]["state"], t.snapshot()[0]["stage"]), ("done", "transcribing"))
        j = t.add(Path("/m/2026-10-20_1800"), summarize=True, documents=1)
        self.assertEqual(next(s for s in t.snapshot()[1]["steps"] if s["key"] == "summarize")["detail"], "with 1 document")

    def test_failure_and_unknown_messages(self):
        self.t.begin(self.id)
        self.t.feed(self.id, "Something else entirely")  # ignored
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
