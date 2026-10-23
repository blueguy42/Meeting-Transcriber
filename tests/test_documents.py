import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt import documents, ocr

TEXT_LINES = ["Rapat Evaluasi Anggaran Kuartal IV 2026", "Pagu anggaran ditetapkan Rp 120.000.000, bukan Rp 150.000.000.",
              "Penanggung jawab daftar vendor: Dina, tenggat Jumat 24 Oktober 2026."]
NEEDS_OCR = unittest.skipUnless(ocr.available(), "Apple Vision (pyobjc-framework-Vision) is not available")


def picture(path: Path, size: int = 30) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    img = Image.new("RGB", (1300, int(size * 1.8) * len(TEXT_LINES) + 30), "white")
    d = ImageDraw.Draw(img)
    for i, t in enumerate(TEXT_LINES):
        d.text((20, 15 + i * int(size * 1.8)), t, font=font, fill=(20, 20, 20))
    img.save(path)
    return path


def read_all(tmp: Path, src: Path, ocr_on=True) -> tuple[dict, str]:
    meeting = tmp / "meeting"
    new, skipped = documents.add(meeting, [src], 12, ocr_on)
    assert not skipped, skipped
    documents.process(meeting, new[0])
    d = documents.get(meeting, new[0]["id"])
    f = documents.extracted_file(meeting, d)
    return d, (f.read_text() if f.exists() else "")


class Manifest(unittest.TestCase):
    def test_names_duplicates_and_limits(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "a").mkdir(); (t / "b").mkdir()
            (t / "a" / "plan.txt").write_text("one"); (t / "b" / "plan.txt").write_text("two")
            (t / "same.txt").write_text("one")  # same content as a/plan.txt
            (t / "x.exe").write_text("no")
            meeting = t / "m"
            new, skipped = documents.add(meeting, [t / "a" / "plan.txt", t / "b" / "plan.txt", t / "same.txt", t / "x.exe", t / "none.pdf"], 5, True)
            self.assertEqual([d["stored"] for d in new], ["plan.txt", "plan (2).txt"])  # same name, different file: both kept
            self.assertEqual(len(skipped), 3)
            self.assertTrue(any("already added" in s for s in skipped))
            self.assertTrue(any("not supported" in s for s in skipped))
            for d in new:
                documents.process(meeting, d)
            self.assertEqual({d["status"] for d in documents.load(meeting)}, {"ready"})
            self.assertEqual((meeting / "documents" / "plan (2).txt").read_text(), "two")

    def test_delete_removes_copy_text_and_entry(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "n.txt").write_text("hello")
            meeting = t / "m"
            new, _ = documents.add(meeting, [t / "n.txt"], 5, False)
            documents.process(meeting, new[0])
            self.assertTrue(documents.extracted_file(meeting, new[0]).exists())
            self.assertTrue(documents.delete(meeting, new[0]["id"]))
            self.assertEqual(documents.load(meeting), [])
            self.assertFalse((meeting / "documents" / "n.txt").exists())
            self.assertFalse(documents.extracted_file(meeting, new[0]).exists())
            self.assertTrue((t / "n.txt").exists())  # your original is never touched
            self.assertFalse(documents.delete(meeting, new[0]["id"]))

    def test_same_file_in_two_meetings_and_after_a_delete(self):
        """A file deleted from one meeting, then added to the next recording, must be copied and read like any other."""
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "a.txt").write_text("Anggaran")
            first, second = t / "m1", t / "m2"
            new, _ = documents.add(first, [t / "a.txt"], 5, False)
            self.assertTrue(documents.delete(first, new[0]["id"]))  # deleted while still queued
            again, _ = documents.add(second, [t / "a.txt"], 7, False)
            self.assertNotEqual(new[0]["id"], again[0]["id"])
            documents.process(second, again[0])
            d = documents.get(second, again[0]["id"])
            self.assertEqual(d["status"], "ready")
            self.assertTrue((second / "documents" / "a.txt").exists())
            self.assertTrue(documents.extracted_file(second, d).exists())

    def test_preview(self):
        p = documents.preview("--- page 1 ---\nAnggaran   Kuartal\nIV " + "x" * 400)
        self.assertTrue(p.startswith("Anggaran Kuartal IV") and p.endswith("…") and len(p) <= 302)


@NEEDS_OCR
class Reading(unittest.TestCase):
    def test_image(self):
        with tempfile.TemporaryDirectory() as t:
            d, text = read_all(Path(t), picture(Path(t) / "scan.png"))
            self.assertEqual(d["status"], "ready")
            self.assertIn("Rp 120.000.000", text)
            self.assertIn("tenggat Jumat 24 Oktober 2026", text)

    def test_image_without_ocr_says_so(self):
        with tempfile.TemporaryDirectory() as t:
            d, text = read_all(Path(t), picture(Path(t) / "scan.png"), ocr_on=False)
            self.assertEqual((d["status"], text), ("nolext", ""))
            self.assertIn("OCR", d["note"])

    def test_scanned_pdf_and_mixed_pdf(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            pic = Image.open(picture(t / "scan.png")).convert("RGB")
            pic.save(t / "scan.pdf", resolution=110)
            d, text = read_all(t, t / "scan.pdf")
            self.assertEqual((d["status"], d["detail"]), ("ready", "1 page"))
            self.assertIn("--- page 1 ---", text)
            self.assertIn("Pagu anggaran ditetapkan", text)
            d, text = read_all(t / "off", t / "scan.pdf", ocr_on=False)  # no text layer, OCR off
            self.assertEqual(d["status"], "nolext")

    def test_word_powerpoint_excel_pictures(self):
        import openpyxl
        import pypandoc
        from openpyxl.drawing.image import Image as XImage
        from pptx import Presentation
        from pptx.util import Inches

        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            img = picture(t / "scan.png")
            (t / "d.md").write_text(f"Ringkasan rapat\n\n![gambar]({img})\n")
            pypandoc.convert_file(str(t / "d.md"), "docx", outputfile=str(t / "d.docx"))
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            slide.shapes.title.text = "Slide vendor"
            slide.shapes.add_picture(str(img), Inches(0.2), Inches(2), width=Inches(9))
            prs.save(t / "s.pptx")
            wb = openpyxl.Workbook(); ws = wb.active; ws["A1"] = "Biaya"; ws["B1"] = 1250000
            ws.add_image(XImage(str(img)), "A3"); wb.save(t / "x.xlsx")
            for name, expect in (("d.docx", "Ringkasan rapat"), ("s.pptx", "Slide vendor"), ("x.xlsx", "Biaya | 1250000")):
                d, text = read_all(t / name.split(".")[0], t / name)
                self.assertEqual(d["status"], "ready", name)
                self.assertIn(expect, text, name)
                self.assertIn("Pagu anggaran ditetapkan", text, name)  # the text inside the picture


class Strips(unittest.TestCase):
    def test_lines_already_in_the_text_layer_are_not_repeated(self):
        self.assertEqual(ocr.new_lines(["Ringkasan Rapat", "Agenda: anggaran", "Total Rp 5"], "Ringkasan Rapat\nAgenda: anggaran"), ["Total Rp 5"])


if __name__ == "__main__":
    unittest.main()


class Summary(unittest.TestCase):
    def test_list_and_markers(self):
        from mt.summarize import documents_block, documents_count, with_document_markers

        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "a.txt").write_text("Anggaran Kuartal IV 2026. Ringkasan pagu per divisi.")
            (t / "b.txt").write_text("Agenda rapat.")
            meeting = t / "m"
            new, _ = documents.add(meeting, [t / "a.txt", t / "b.txt"], 3, False)
            new[1]["added_at"] = 65
            documents.update(meeting, new[1]["id"], added_at=65)
            for d in documents.load(meeting):
                documents.process(meeting, d)
            cfg = {"use_documents": True}
            block = documents_block(meeting, cfg)
            self.assertIn("1. a.txt", block)
            self.assertIn("added at [00:03]", block)
            self.assertIn("added at [01:05]", block)
            self.assertIn("text: documents/extracted/a.txt.txt", block)
            self.assertIn('preview: "Anggaran Kuartal IV 2026.', block)
            self.assertEqual(documents_count(meeting, cfg), 2)
            self.assertEqual(documents_block(meeting, {"use_documents": False}), "")
            documents.delete(meeting, new[0]["id"])  # deleted: no longer listed, no marker
            self.assertNotIn("a.txt", documents_block(meeting, cfg))
            docs = documents.load(meeting)
            marked = with_document_markers("# Transcript\n[00:00:10] Me: hi\n[00:01:30] Others: ok\n", docs)
            self.assertEqual(marked.split("\n")[2], "[00:01:05] (document added: documents/b.txt)")
            self.assertEqual(with_document_markers("[00:00:10] Me: hi", docs).split("\n")[-1], "[00:01:05] (document added: documents/b.txt)")
