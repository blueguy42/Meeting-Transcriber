"""The pages shown in the floating bar's panels and in the Settings window.

A page is a folder: `<name>/<name>.html` with its stylesheets and scripts beside it (`shared/` holds what every page uses).
A web view that is given a string has no folder to load files from, so `load_page` puts the files into the HTML itself."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_STYLESHEET = re.compile(r'<link rel="stylesheet" href="([^"]+)">')
_SCRIPT = re.compile(r'<script src="([^"]+)"></script>')


def load_page(name: str) -> str:
    """The HTML of a page with its stylesheets and scripts written into it, in the order the page lists them."""
    folder = ROOT / name
    html = (folder / f"{name}.html").read_text()

    def inline(tag: str):
        return lambda m: f"<{tag}>\n{(folder / m.group(1)).read_text()}</{tag}>"

    return _SCRIPT.sub(inline("script"), _STYLESHEET.sub(inline("style"), html))
