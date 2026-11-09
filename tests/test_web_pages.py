import re
import unittest

from meeting_transcriber import web


class Pages(unittest.TestCase):
    def test_every_page_is_complete_once_its_files_are_written_into_it(self):
        for name in ("overlay", "popup", "notes", "settings"):
            with self.subTest(page=name):
                html = web.load_page(name)
                self.assertNotIn('<link rel="stylesheet"', html)  # all included
                self.assertNotRegex(html, r'<script src=')
                self.assertIn("<style>", html)
                self.assertEqual(html.count("<script>"), html.count("</script>"))
                self.assertTrue(html.lstrip().startswith("<!doctype html>"))

    def test_the_pages_that_talk_to_the_app_have_the_bridge(self):
        for name in ("overlay", "popup", "notes"):
            self.assertIn("function post(msg)", web.load_page(name))

    def test_a_script_cannot_end_the_page_early(self):
        for path in web.ROOT.glob("*/*.js"):
            self.assertNotIn("</script", path.read_text().lower(), path.name)  # it would end the inline <script> it is written into

    def test_every_file_a_page_lists_exists(self):
        for html in web.ROOT.glob("*/*.html"):
            for ref in re.findall(r'(?:href|src)="([^"]+)"', html.read_text()):
                self.assertTrue((html.parent / ref).is_file(), f"{html.name}: {ref}")

    def test_the_files_of_a_page_are_all_used(self):
        for folder in (p for p in web.ROOT.iterdir() if p.is_dir() and p.name != "shared" and p.name != "__pycache__"):
            html = (folder / f"{folder.name}.html").read_text()
            for f in folder.iterdir():
                if f.suffix in (".css", ".js"):
                    self.assertIn(f'"{f.name}"', html, f"{folder.name}/{f.name} is not included")


if __name__ == "__main__":
    unittest.main()
